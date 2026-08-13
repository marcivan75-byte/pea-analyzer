from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import re
import unicodedata

import pandas as pd
from bs4 import BeautifulSoup

SOURCE = "Boursorama"


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _num(value: object) -> float | None:
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    match = re.search(r"[-+]?\d[\d ]*(?:[,.]\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _explicit_risk(value: object) -> float | None:
    """Accept only an actually observed SRI numerator.

    Boursorama's rendered ETF table can expose only `/7` when the graphical
    numerator is not present in text. That must remain missing; `/7` is never
    interpreted as risk 7/7.
    """
    text = str(value or "").strip()
    ratio = re.search(r"(?<!\d)([1-7])\s*/\s*7(?!\d)", text)
    if ratio:
        return float(ratio.group(1))
    if re.fullmatch(r"[1-7](?:[,.]0+)?", text):
        return float(text.replace(",", "."))
    return None


def _canonical_url(soup: BeautifulSoup) -> str:
    node = soup.find("link", attrs={"rel": "canonical"})
    if node and node.get("href"):
        return str(node.get("href"))
    node = soup.find("meta", attrs={"property": "og:url"})
    return str(node.get("content")) if node and node.get("content") else ""


def _tables(html: str) -> list[pd.DataFrame]:
    try:
        frames = pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except (ValueError, ImportError):
        return []
    out = []
    for frame in frames:
        f = frame.copy()
        if isinstance(f.columns, pd.MultiIndex):
            f.columns = [" | ".join(str(x).strip() for x in col if str(x).strip() and not str(x).startswith("Unnamed")) for col in f.columns]
        else:
            f.columns = [str(c).strip() for c in f.columns]
        out.append(f)
    return out


def _col(frame: pd.DataFrame, *needles: str) -> str | None:
    for col in frame.columns:
        normalized = _norm(col)
        if any(_norm(needle) in normalized for needle in needles):
            return str(col)
    return None


def _name_map(etfs: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    duplicates: set[str] = set()
    if "isin" not in etfs.columns or "name" not in etfs.columns:
        return mapping
    for _, row in etfs.iterrows():
        key = _norm(row.get("name"))
        isin = str(row.get("isin") or "").strip()
        if not key or not isin:
            continue
        if key in mapping and mapping[key] != isin:
            duplicates.add(key)
        else:
            mapping[key] = isin
    for key in duplicates:
        mapping.pop(key, None)
    return mapping


def _obs(isin: str, field: str, value, url: str, source_file: str, provider: str = "Morningstar") -> dict:
    return {
        "universe": "ETF",
        "isin": isin,
        "field": field,
        "value": value,
        "source": f"{SOURCE}/{provider}" if provider else SOURCE,
        "source_url": url,
        "source_file": source_file,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "evidence_level": "B",
        "validation_status": "ATTRIBUTED",
    }


def parse_etf_html_safe(html: str, etfs: pd.DataFrame, source_file: str = "") -> tuple[list[dict], list[dict], dict]:
    soup = BeautifulSoup(html, "lxml")
    url = _canonical_url(soup)
    if "boursorama.com" not in url:
        return [], [{"source": SOURCE, "source_file": source_file, "reason": "BOURSORAMA_SOURCE_URL_MISSING"}], {"matched_rows": 0}
    canonical = set(etfs["isin"].astype(str).str.strip()) if "isin" in etfs.columns else set()
    names = _name_map(etfs)
    observations: list[dict] = []
    failures: list[dict] = []
    matched: set[str] = set()
    explicit_risk = 0
    risk_unobserved = 0

    for frame in _tables(html):
        name_col = _col(frame, "libelle", "nom")
        isin_col = _col(frame, "isin")
        rating_col = _col(frame, "morningstar", "notation")
        risk_col = _col(frame, "risque")
        category_col = _col(frame, "categorie morningstar")
        perf_col = _col(frame, "perf 1 an")
        price_col = _col(frame, "dernier")
        if not name_col and not isin_col:
            continue
        if not rating_col and not risk_col and not category_col:
            continue
        for _, row in frame.iterrows():
            isin = str(row.get(isin_col) or "").strip() if isin_col else ""
            if isin not in canonical and name_col:
                isin = names.get(_norm(row.get(name_col)), "")
            if not isin or isin not in canonical:
                continue
            matched.add(isin)
            if rating_col:
                rating = _num(row.get(rating_col))
                if rating is not None and 0 <= rating <= 5:
                    observations.append(_obs(isin, "morningstar_rating", rating, url, source_file))
                    observations.append(_obs(isin, "boursorama_morningstar_rating", rating, url, source_file))
            if risk_col:
                risk = _explicit_risk(row.get(risk_col))
                if risk is not None:
                    explicit_risk += 1
                    observations.append(_obs(isin, "risk_indicator", risk, url, source_file))
                    observations.append(_obs(isin, "boursorama_risk_indicator", risk, url, source_file))
                elif str(row.get(risk_col) or "").strip():
                    risk_unobserved += 1
            if category_col:
                category = str(row.get(category_col) or "").strip()
                if category and category.casefold() != "nan":
                    observations.append(_obs(isin, "morningstar_category", category, url, source_file))
                    observations.append(_obs(isin, "boursorama_morningstar_category", category, url, source_file))
            if perf_col:
                perf = _num(row.get(perf_col))
                if perf is not None:
                    observations.append(_obs(isin, "boursorama_perf_1y_pct", perf, url, source_file, provider=""))
            if price_col:
                price = _num(row.get(price_col))
                if price is not None:
                    observations.append(_obs(isin, "boursorama_last_price", price, url, source_file, provider=""))

    if not matched:
        failures.append({"source": SOURCE, "source_file": source_file, "reason": "NO_CANONICAL_ETF_MATCH"})
    if risk_unobserved:
        failures.append({
            "source": SOURCE,
            "source_file": source_file,
            "reason": "ETF_RISK_GRAPHIC_NUMERATOR_NOT_OBSERVED",
            "rows": risk_unobserved,
            "governance": "Risk remains N/A; a rendered '/7' is never converted to 7/7.",
        })
    return observations, failures, {
        "matched_rows": len(matched),
        "observations": len(observations),
        "explicit_risk_rows": explicit_risk,
        "risk_unobserved_rows": risk_unobserved,
        "source_url": url,
    }


def load_etf_pages(root: Path, etfs: pd.DataFrame, relative_root: str = "inputs/boursorama_snapshots") -> tuple[list[dict], list[dict], dict]:
    directory = root / relative_root
    if not directory.exists():
        return [], [], {"files": 0, "matched_rows": 0, "observations": 0}
    observations: list[dict] = []
    failures: list[dict] = []
    files = 0
    matched = 0
    explicit_risk = 0
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        lower = html.casefold()
        if "morningstar" not in lower or ("tracker" not in lower and "etf" not in lower):
            continue
        obs, failed, stats = parse_etf_html_safe(html, etfs, str(path))
        if stats.get("matched_rows", 0) == 0:
            continue
        files += 1
        matched += int(stats.get("matched_rows", 0))
        explicit_risk += int(stats.get("explicit_risk_rows", 0))
        observations.extend(obs)
        failures.extend(failed)
    return observations, failures, {
        "files": files,
        "matched_rows": matched,
        "observations": len(observations),
        "explicit_risk_rows": explicit_risk,
        "failures": len(failures),
    }
