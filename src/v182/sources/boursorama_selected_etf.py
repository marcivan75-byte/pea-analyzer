from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any

from bs4 import BeautifulSoup

CACHE_VERSION = "BOURSORAMA_SELECTED_ETF_V1_2_MS_SRI_PALMARES_SHADOW"

_STAR_OK = {1.0, 2.0, 3.0, 4.0, 5.0}
_SRI_OK = {1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0}


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


def _stars_from_aria(label: str) -> float | None:
    match = re.search(r"([0-5])\s*(?:\u00e9toiles?|etoiles?|stars?)\s*sur\s*5", label or "", flags=re.IGNORECASE)
    if not match:
        match = re.search(r"([1-5])\s*(?:\u00e9toiles?|etoiles?|stars?)", label or "", flags=re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    return value if value in _STAR_OK else None


def merge_ms_sri_fields(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Keep a resolved OK value; never let a later page wipe it with BLOCK_MISSING."""
    out = dict(base)
    for key, value in incoming.items():
        if key.endswith("_parse_status") and str(out.get(key)) == "OK" and str(value) != "OK":
            continue
        if key.endswith("_stars") and out.get(key) in _STAR_OK and value not in _STAR_OK:
            continue
        if key.endswith("_sri_risk") and out.get(key) in _SRI_OK and value not in _SRI_OK:
            continue
        out[key] = value
    return out


def parse_etf_morningstar_sri_html(html: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        soup = BeautifulSoup("", "html.parser")
    text = " ".join(soup.stripped_strings)
    stars = None
    star_source = None
    for fieldset in soup.select("fieldset.c-rating"):
        stars = _stars_from_aria(str(fieldset.get("aria-label") or ""))
        if stars is not None:
            star_source = "ARIA_LABEL"
            break
        checked = fieldset.select_one("input.c-rating__check[checked], input[checked]")
        if checked is not None:
            value = _num(checked.get("value"))
            if value in _STAR_OK:
                stars = value
                star_source = "CHECKED_RADIO"
                break
    if stars is None:
        for selector in (".c-analysis__morningstar", ".c-notation-morningstar", "fieldset.c-rating"):
            root = soup.select_one(selector)
            if root is None:
                continue
            on_icons = [
                node
                for node in root.select("[class*='star'][class*='on'], [class*='star'][class*='full'], .c-star--on")
                if getattr(node, "name", "") != "input"
            ]
            if 1 <= len(on_icons) <= 5:
                stars = float(len(on_icons))
                star_source = "SCOPED_ICONS"
                break
    if stars is None:
        match = re.search(
            r"Notation morningstar[^/]{0,160}?([1-5])\s*(?:/5|\u00e9toiles?|etoiles?|stars?)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            stars = float(match.group(1))
            star_source = "TEXT"
    if stars in _STAR_OK:
        fields["boursorama_etf_morningstar_stars"] = stars
        fields["boursorama_etf_morningstar_parse_status"] = "OK"
        fields["boursorama_etf_morningstar_parse_source"] = star_source
        asof = re.search(
            r"Notation morningstar[^\d]{0,80}du\s+(\d{1,2}\s+\w+\.?\s+\d{4})",
            text,
            flags=re.IGNORECASE,
        )
        if asof:
            fields["boursorama_etf_morningstar_asof_raw"] = asof.group(1)
    elif soup.select_one("fieldset.c-rating") is not None or re.search(r"notation morningstar", text, flags=re.IGNORECASE):
        fields["boursorama_etf_morningstar_parse_status"] = "ICONS_UNRESOLVED"
    else:
        fields["boursorama_etf_morningstar_parse_status"] = "BLOCK_MISSING"

    sri = None
    sri_source = None
    for gauge in soup.select("[data-gauge-current-step]"):
        steps = _num(gauge.get("data-gauge-steps"))
        current = _num(gauge.get("data-gauge-current-step"))
        if current in _SRI_OK and steps in (None, 7.0):
            parent_text = " ".join(gauge.parent.stripped_strings) if gauge.parent else ""
            if "risque" in parent_text.lower() or "sri" in parent_text.lower() or steps == 7.0:
                sri = current
                sri_source = "GAUGE_ATTR"
                break
    if sri is None:
        match = re.search(r"Risque du fonds[^\d]{0,120}([1-7])\s*/\s*7", text, flags=re.IGNORECASE)
        if match:
            sri = float(match.group(1))
            sri_source = "TEXT"
    if sri in _SRI_OK:
        fields["boursorama_etf_sri_risk"] = sri
        fields["boursorama_etf_sri_parse_status"] = "OK"
        fields["boursorama_etf_sri_parse_source"] = sri_source
    else:
        fields["boursorama_etf_sri_parse_status"] = "ICONS_UNRESOLVED"
    return fields


def parse_etf_palmares_rows(html: str) -> dict[str, dict[str, object]]:
    """Map Boursorama tracker codes to numeric Morningstar / SRI from palmares tables."""
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:
        return {}
    rows: dict[str, dict[str, object]] = {}
    for row in soup.select("table tr"):
        link = row.select_one("a[href*='/bourse/trackers/cours/']")
        if link is None:
            continue
        href = str(link.get("href") or "")
        match = re.search(r"/bourse/trackers/cours/([^/]+)/?", href)
        if not match:
            continue
        code = match.group(1).strip()
        payload: dict[str, object] = {"boursorama_code": code}
        fieldset = row.select_one("fieldset.c-rating")
        stars = _stars_from_aria(str(fieldset.get("aria-label") or "")) if fieldset is not None else None
        if stars is None and fieldset is not None:
            checked = fieldset.select_one("input[checked]")
            value = _num(checked.get("value")) if checked is not None else None
            if value in _STAR_OK:
                stars = value
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
        if stars is None and cells:
            last = _num(cells[-1])
            if last in _STAR_OK:
                stars = last
        if stars in _STAR_OK:
            payload["boursorama_etf_morningstar_stars"] = stars
            payload["boursorama_etf_morningstar_parse_status"] = "OK"
            payload["boursorama_etf_morningstar_parse_source"] = "PALMARES_NUMERIC"
        gauge = row.select_one("[data-gauge-current-step]")
        sri = _num(gauge.get("data-gauge-current-step")) if gauge is not None else None
        if sri in _SRI_OK:
            payload["boursorama_etf_sri_risk"] = sri
            payload["boursorama_etf_sri_parse_status"] = "OK"
            payload["boursorama_etf_sri_parse_source"] = "PALMARES_GAUGE"
        if len(payload) > 1:
            rows[code] = payload
    return rows


def palmares_search_url(isin: str) -> str:
    safe = str(isin or "").strip()
    return (
        "https://www.boursorama.com/bourse/trackers/recherche/"
        f"?search%5Bkeywords%5D={safe}"
        "&etfSearch%5BisEtf%5D=1"
        "&beginnerEtfSearch%5Beligibility%5D%5B%5D=taxation"
    )


# Explicit aliases preserve the historical public import contract while letting
# the implementation live in dedicated modules. Ruff recognizes these as
# intentional re-exports and will not remove them during maintenance --fix.
from v182.sources.boursorama_etf_sheet import parse_etf_risk_html as parse_etf_risk_html
from v182.sources.boursorama_etf_sheet import parse_etf_sheet_html as parse_etf_sheet_html
from v182.sources.boursorama_etf_collect import (
    collect_selected_etf_context_cached as collect_selected_etf_context_cached,
)
