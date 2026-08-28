from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
import json
import math
import re
from typing import Callable

from bs4 import BeautifulSoup
import pandas as pd

from v182.sources.boursorama_public import boursorama_code, etf_urls
from v182.sources.rate_limit import StartRateLimiter

CACHE_VERSION = "BOURSORAMA_SELECTED_ETF_V1_1_MS_SRI_SHADOW"


@dataclass(frozen=True)
class BoursoramaSelectedETFResult:
    observations: list[dict]
    failures: list[dict]
    metrics: dict


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_hours(value: object, now: datetime) -> float:
    parsed = _parse_utc(value)
    if parsed is None:
        return math.inf
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


def _text(html: str) -> str:
    try:
        return " ".join(BeautifulSoup(html, "lxml").stripped_strings)
    except Exception:
        return ""


def _num(value: object) -> float | None:
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    text = re.sub(r"[^0-9,+.\- ]", "", text).replace(" ", "")
    if not text or text in {"-", "+"}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _capture(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return " ".join(match.group(1).split()) if match else None


def _capture_num(text: str, pattern: str) -> float | None:
    value = _capture(text, pattern)
    return _num(value) if value is not None else None


def parse_etf_morningstar_sri_html(html: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        soup = BeautifulSoup("", "html.parser")
    text = " ".join(soup.stripped_strings)
    stars = None
    block = soup.find(string=re.compile(r"Notation morningstar", re.I))
    root = None
    if block is not None:
        root = block.find_parent()
        for _ in range(6):
            if root is None or root.find_parent() is None:
                break
            root = root.find_parent()
            classes = " ".join(root.get("class") or []) if hasattr(root, "get") else ""
            if re.search(r"notation|morningstar|analyse", classes, re.I):
                break
    search_root = root if root is not None else soup
    on_icons = search_root.select("[class*='star'][class*='on'], [class*='star'][class*='full'], [class*='c-star--on'], svg[class*='star'][class*='full']")
    if 1 <= len(on_icons) <= 5:
        stars = float(len(on_icons))
    if stars is None:
        aria = search_root.find(attrs={"aria-label": re.compile(r"([1-5])\s*(\u00e9toile|etoile|star)", re.I)})
        if aria is not None:
            match = re.search(r"([1-5])", str(aria.get("aria-label") or ""))
            if match:
                stars = float(match.group(1))
    if stars is None:
        match = re.search(r"Notation morningstar[^/]{0,120}?([1-5])\s*(?:/5|\u00e9toiles?|etoiles?|stars?)", text, flags=re.IGNORECASE)
        if match:
            stars = float(match.group(1))
    if stars in {1.0, 2.0, 3.0, 4.0, 5.0}:
        fields["boursorama_etf_morningstar_stars"] = stars
        fields["boursorama_etf_morningstar_parse_status"] = "OK"
        asof = re.search(r"Notation morningstar[^\d]{0,80}du\s+(\d{1,2}\s+\w+\.?\s+\d{4})", text, flags=re.IGNORECASE)
        if asof:
            fields["boursorama_etf_morningstar_asof_raw"] = asof.group(1)
    elif re.search(r"notation morningstar", text, flags=re.IGNORECASE):
        fields["boursorama_etf_morningstar_parse_status"] = "ICONS_UNRESOLVED"
    else:
        fields["boursorama_etf_morningstar_parse_status"] = "BLOCK_MISSING"
    sri = None
    sri_node = soup.find(string=re.compile(r"Risque du fonds", re.I))
    if sri_node is not None:
        parent = sri_node.find_parent()
        chunk = " ".join((parent or soup).stripped_strings)
        html_chunk = str(parent or "")
        match = re.search(r"([1-7])\s*/\s*7", html_chunk) or re.search(r"([1-7])\s*/\s*7", chunk)
        if match:
            sri = float(match.group(1))
    if sri is None:
        match = re.search(r"Risque du fonds[^\d]{0,120}([1-7])\s*/\s*7", text, flags=re.IGNORECASE)
        if match:
            sri = float(match.group(1))
    if sri in {1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0}:
        fields["boursorama_etf_sri_risk"] = sri
        fields["boursorama_etf_sri_parse_status"] = "OK"
    else:
        fields["boursorama_etf_sri_parse_status"] = "ICONS_UNRESOLVED"
    return fields


from v182.sources.boursorama_etf_sheet import parse_etf_risk_html, parse_etf_sheet_html
from v182.sources.boursorama_etf_collect import collect_selected_etf_context_cached
