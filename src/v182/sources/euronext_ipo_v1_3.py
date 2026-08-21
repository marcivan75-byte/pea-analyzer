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
MAX_PAGINATION_PAGES = 80


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


def _page_url(page_index: int) -> str:
    if page_index <= 0:
        return EURONEXT_IPO_ALL
    return f"{EURONEXT_IPO_ALL}?page={page_index}"


def _ipo_tables(html: str) -> list[pd.DataFrame]:
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return []
    return [
        table
        for table in tables
        if "company name" in {str(column).strip().lower() for column in table.columns}
        and "date" in {str(column).strip().lower() for column in table.columns}
    ]


def _page_signature(tables: list[pd.DataFrame]) -> tuple[str, ...]:
    values: list[str] = []
    for table in tables:
        columns = {str(column).strip().lower(): column for column in table.columns}
        for _, row in table.iterrows():
            values.append(
                "|".join(
                    (
                        str(row.get(columns.get("isin code", ""), "") or "").strip().upper(),
                        str(row.get(columns.get("date", ""), "") or "").strip(),
                        _norm(row.get(columns.get("company name", ""), "")),
                        str(row.get(columns.get("ticker", ""), "") or "").strip().upper(),
                    )
                )
            )
    return tuple(sorted(values))


def collect_euronext_v1_3(
    start: date,
    end: date,
    timeout: int = 20,
    *,
    max_pages: int = MAX_PAGINATION_PAGES,
) -> tuple[list[dict], dict]:
    """Collect the official Euronext IPO showcase across historical pages.

    Pagination is sequential and fail-closed. The collector stops on an empty
    IPO page, a repeated page signature, the first page wholly older than the
    requested start date, or the explicit safety cap. Candidate identity still
    comes from the official table ISIN; no name/ticker inference is promoted.
    """
    source = "EURONEXT"
    headers = legacy._http_headers()
    candidates: list[dict] = []
    seen_candidate_keys: set[tuple[str, str, str, str]] = set()
    seen_page_signatures: set[tuple[str, ...]] = set()
    detail_success = 0
    detail_failed = 0
    duplicate_candidates = 0
    rows_seen = 0
    rows_in_requested_range = 0
    pages_fetched = 0
    stop_reason = "MAX_PAGE_CAP_REACHED"
    pagination_complete = False

    if max_pages < 1:
        raise ValueError("EURONEXT_MAX_PAGES_MUST_BE_POSITIVE")

    for page_index in range(max_pages):
        page_url = _page_url(page_index)
        try:
            response = requests.get(page_url, headers=headers, timeout=timeout)
            response.raise_for_status()
        except Exception as exc:
            if page_index == 0:
                return [], {
                    "source": source,
                    "status": "FAILED",
                    "count": 0,
                    "pages_fetched": 0,
                    "pagination_complete": False,
                    "stop_reason": "FIRST_PAGE_FETCH_FAILED",
                    "detail": f"{type(exc).__name__}: {str(exc)[:180]}",
                }
            stop_reason = "PAGE_FETCH_FAILED"
            break

        pages_fetched += 1
        html = response.text
        tables = _ipo_tables(html)
        signature = _page_signature(tables)
        if not signature:
            stop_reason = "EMPTY_OR_NO_VALID_IPO_ROWS"
            pagination_complete = True
            break
        if signature in seen_page_signatures:
            stop_reason = "REPEATED_PAGE_SIGNATURE"
            pagination_complete = True
            break
        seen_page_signatures.add(signature)

        detail_links = _showcase_links(html)
        page_dates: list[date] = []
        for table in tables:
            columns = {str(column).strip().lower(): column for column in table.columns}
            for _, row in table.iterrows():
                rows_seen += 1
                parsed = legacy._parse_date(row.get(columns["date"]))
                if not parsed:
                    continue
                page_dates.append(parsed)
                if not (start <= parsed <= end):
                    continue
                rows_in_requested_range += 1

                name = row.get(columns["company name"])
                isin = str(row.get(columns.get("isin code", ""), "") or "").strip()
                symbol = str(row.get(columns.get("ticker", ""), "") or "").strip()
                location = str(row.get(columns.get("location", ""), "") or "").strip()
                market = str(row.get(columns.get("market", ""), "") or "").strip()
                candidate_key = (
                    isin.upper(),
                    parsed.isoformat(),
                    symbol.upper(),
                    _norm(name),
                )
                if candidate_key in seen_candidate_keys:
                    duplicate_candidates += 1
                    continue
                seen_candidate_keys.add(candidate_key)

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
                    symbol=symbol or None,
                    exchange=market or "EURONEXT",
                    expected_date=parsed,
                    status="expected",
                    source=source,
                    isin=isin,
                    euronext_location=location,
                    issuer_country_hint=isin[:2].upper() if len(isin) >= 2 else "",
                    euronext_source_page=page_url,
                    **detail,
                )
                official_price = legacy._as_float(candidate.get("euronext_ipo_price"))
                if official_price is not None and candidate.get("price_mid") in (None, ""):
                    candidate["price_low"] = official_price
                    candidate["price_high"] = official_price
                    candidate["price_mid"] = official_price
                    candidate["price_range"] = str(
                        candidate.get("euronext_ipo_price_text") or official_price
                    )
                    candidate["price_evidence_source"] = "EURONEXT_OFFICIAL_SHOWCASE"
                candidates.append(candidate)

        if page_dates and max(page_dates) < start:
            stop_reason = "PAGE_WHOLELY_BEFORE_REQUESTED_START"
            pagination_complete = True
            break
    else:
        stop_reason = "MAX_PAGE_CAP_REACHED"

    status = "SUCCESS" if pagination_complete else "PARTIAL"
    return candidates, {
        "source": source,
        "status": status,
        "count": len(candidates),
        "pages_fetched": pages_fetched,
        "rows_seen": rows_seen,
        "rows_in_requested_range": rows_in_requested_range,
        "duplicate_candidates_removed": duplicate_candidates,
        "detail_enriched_count": detail_success,
        "detail_failed_count": detail_failed,
        "pagination_complete": pagination_complete,
        "stop_reason": stop_reason,
        "max_pages": max_pages,
        "detail_policy": "OFFICIAL_SHOWCASE_ONLY_NO_INFERENCE",
        "identity_policy": "EXACT_OFFICIAL_ISIN_ONLY",
    }
