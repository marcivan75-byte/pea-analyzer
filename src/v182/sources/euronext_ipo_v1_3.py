from __future__ import annotations

from datetime import date
from io import StringIO
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import pandas as pd
import requests

from v182.decision import ipo_radar_v1 as legacy

EURONEXT_IPO_ALL = "https://live.euronext.com/en/ipo-showcase/all"


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    return [text.strip() for text in soup.stripped_strings if text and text.strip()]


def _field_after_label(lines: list[str], label: str) -> str:
    target = _norm(label)
    for index, value in enumerate(lines):
        if _norm(value) != target:
            continue
        for candidate in lines[index + 1 : index + 5]:
            if candidate.strip():
                return candidate.strip()
    return ""


def parse_ipo_price(value: object) -> tuple[float | None, str]:
    text = str(value or "").strip()
    if not text:
        return None, ""
    currency = ""
    upper = text.upper()
    currency_patterns = (
        ("EUR", ("EUR", "€")),
        ("NOK", ("NOK",)),
        ("SEK", ("SEK",)),
        ("DKK", ("DKK",)),
        ("GBP", ("GBP", "£")),
        ("CHF", ("CHF",)),
        ("USD", ("USD", "$")),
    )
    for code, markers in currency_patterns:
        if any(marker in upper if marker.isalpha() else marker in text for marker in markers):
            currency = code
            break
    numbers = re.findall(r"(?<!\d)(\d{1,8}(?:[.,]\d{1,6})?)(?!\d)", text)
    if not numbers:
        return None, currency
    try:
        price = float(numbers[-1].replace(",", "."))
    except ValueError:
        return None, currency
    return (price if price > 0 else None), currency


def parse_showcase_detail(html: str, url: str = "") -> dict:
    lines = _lines(html)
    icb = _field_after_label(lines, "ICB")
    icb_match = re.match(r"\s*(\d{4,10})\s*(.*)", icb)
    price_text = _field_after_label(lines, "IPO price")
    price, currency = parse_ipo_price(price_text)
    return {
        "euronext_showcase_url": url,
        "euronext_detail_status": "SUCCESS" if lines else "EMPTY",
        "euronext_icb_code": icb_match.group(1) if icb_match else "",
        "euronext_icb_name": icb_match.group(2).strip() if icb_match else icb,
        "euronext_website": _field_after_label(lines, "Website address"),
        "euronext_ipo_date_text": _field_after_label(lines, "IPO date"),
        "euronext_ipo_price_text": price_text,
        "euronext_ipo_price": price,
        "euronext_ipo_currency": currency,
        "euronext_ipo_type": _field_after_label(lines, "IPO type"),
    }


def _showcase_links(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    links: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        text = anchor.get_text(" ", strip=True)
        if not text or "/ipo-showcase/" not in href or href.rstrip("/").endswith("/ipo-showcase/all"):
            continue
        links[_norm(text)] = urljoin(EURONEXT_IPO_ALL, href)
    return links


def _fetch_detail(url: str, headers: dict[str, str], timeout: int) -> dict:
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return parse_showcase_detail(response.text, url)
    except Exception as exc:
        return {
            "euronext_showcase_url": url,
            "euronext_detail_status": "FAILED",
            "euronext_detail_error": f"{type(exc).__name__}: {str(exc)[:160]}",
        }


def collect_euronext_v1_3(start: date, end: date, timeout: int = 20) -> tuple[list[dict], dict]:
    """Collect Euronext IPO rows and attach official showcase evidence when available."""
    source = "EURONEXT"
    headers = legacy._http_headers()
    try:
        response = requests.get(EURONEXT_IPO_ALL, headers=headers, timeout=timeout)
        response.raise_for_status()
        html = response.text
        tables = pd.read_html(StringIO(html))
        detail_links = _showcase_links(html)
        candidates: list[dict] = []
        detail_success = 0
        detail_failed = 0
        for table in tables:
            columns = {str(col).strip().lower(): col for col in table.columns}
            if "company name" not in columns or "date" not in columns:
                continue
            for _, row in table.iterrows():
                parsed = legacy._parse_date(row.get(columns["date"]))
                if not parsed or not (start <= parsed <= end):
                    continue
                name = row.get(columns["company name"])
                isin = str(row.get(columns.get("isin code", ""), "") or "").strip()
                location = str(row.get(columns.get("location", ""), "") or "").strip()
                market = str(row.get(columns.get("market", ""), "") or "").strip()
                detail_url = detail_links.get(_norm(name), "")
                detail: dict = {}
                if detail_url:
                    detail = _fetch_detail(detail_url, headers, timeout)
                    if detail.get("euronext_detail_status") == "SUCCESS":
                        detail_success += 1
                    else:
                        detail_failed += 1
                candidate = legacy._standard_candidate(
                    name=name,
                    symbol=row.get(columns.get("ticker", "")) if "ticker" in columns else None,
                    exchange=market or "EURONEXT",
                    expected_date=parsed,
                    status="expected",
                    source=source,
                    isin=isin,
                    euronext_location=location,
                    issuer_country_hint=isin[:2].upper() if len(isin) >= 2 else "",
                    **detail,
                )
                official_price = legacy._as_float(candidate.get("euronext_ipo_price"))
                if official_price is not None and candidate.get("price_mid") in (None, ""):
                    candidate["price_low"] = official_price
                    candidate["price_high"] = official_price
                    candidate["price_mid"] = official_price
                    candidate["price_range"] = str(candidate.get("euronext_ipo_price_text") or official_price)
                    candidate["price_evidence_source"] = "EURONEXT_OFFICIAL_SHOWCASE"
                candidates.append(candidate)
        return candidates, {
            "source": source,
            "status": "SUCCESS",
            "count": len(candidates),
            "detail_enriched_count": detail_success,
            "detail_failed_count": detail_failed,
            "detail_policy": "OFFICIAL_SHOWCASE_ONLY_NO_INFERENCE",
        }
    except Exception as exc:
        return [], {
            "source": source,
            "status": "FAILED",
            "count": 0,
            "detail": f"{type(exc).__name__}: {str(exc)[:180]}",
        }
