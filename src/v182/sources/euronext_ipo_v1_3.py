from __future__ import annotations

from datetime import date, datetime
from io import StringIO
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import pandas as pd
import requests

from v182.decision import ipo_radar_v1 as legacy

EURONEXT_IPO_ALL = "https://live.euronext.com/en/ipo-showcase/all"
DEFAULT_MAX_PAGES = 100


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _parse_catalogue_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(0))
        except ValueError:
            return None
    european_match = re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", text)
    if european_match:
        try:
            return date(
                int(european_match.group(3)),
                int(european_match.group(2)),
                int(european_match.group(1)),
            )
        except ValueError:
            return None
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date()


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


def _page_url(page: int) -> str:
    if page == 0:
        return EURONEXT_IPO_ALL
    return f"{EURONEXT_IPO_ALL}?page={page}"


def _table_rows(html: str) -> tuple[list[tuple[dict[str, object], dict[str, object]]], list[date]]:
    rows: list[tuple[dict[str, object], dict[str, object]]] = []
    page_dates: list[date] = []
    for table in pd.read_html(StringIO(html)):
        columns = {str(col).strip().lower(): col for col in table.columns}
        if "company name" not in columns or "date" not in columns:
            continue
        for _, row in table.iterrows():
            parsed = _parse_catalogue_date(row.get(columns["date"]))
            if parsed:
                page_dates.append(parsed)
            rows.append((row.to_dict(), columns))
    return rows, page_dates


def collect_euronext_v1_3(
    start: date,
    end: date,
    timeout: int = 20,
    max_pages: int = DEFAULT_MAX_PAGES,
    target_isins: set[str] | None = None,
    enrich_details: bool = True,
) -> tuple[list[dict], dict]:
    """Collect the paginated official Euronext IPO catalogue fail-closed."""
    if start > end:
        raise ValueError("EURONEXT_IPO_INVALID_DATE_RANGE")
    if max_pages < 1:
        raise ValueError("EURONEXT_IPO_INVALID_MAX_PAGES")

    normalized_targets = None
    if target_isins is not None:
        normalized_targets = {
            str(value).strip().upper()
            for value in target_isins
            if str(value).strip()
        }

    source = "EURONEXT"
    headers = legacy._http_headers()
    candidates: list[dict] = []
    detail_success = 0
    detail_failed = 0
    pages_fetched = 0
    stop_reason = "MAX_PAGES_REACHED"
    non_target_rows_skipped = 0
    seen_page_fingerprints: set[tuple[tuple[str, str, str], ...]] = set()
    seen_candidates: set[tuple[str, str, str, str, str]] = set()

    try:
        for page in range(max_pages):
            page_url = _page_url(page)
            response = requests.get(page_url, headers=headers, timeout=timeout)
            response.raise_for_status()
            html = response.text
            rows, page_dates = _table_rows(html)
            pages_fetched += 1

            if not rows:
                stop_reason = "NO_IPO_ROWS"
                break

            fingerprint_items: list[tuple[str, str, str]] = []
            for row, columns in rows:
                parsed = _parse_catalogue_date(row.get(columns["date"]))
                name = str(row.get(columns["company name"], "") or "").strip()
                isin = str(row.get(columns.get("isin code", ""), "") or "").strip().upper()
                fingerprint_items.append((parsed.isoformat() if parsed else "", isin, _norm(name)))
            fingerprint = tuple(fingerprint_items)
            if fingerprint in seen_page_fingerprints:
                stop_reason = "REPEATED_PAGE"
                break
            seen_page_fingerprints.add(fingerprint)

            detail_links = _showcase_links(html) if enrich_details else {}
            for row, columns in rows:
                parsed = _parse_catalogue_date(row.get(columns["date"]))
                if not parsed or not (start <= parsed <= end):
                    continue
                name = row.get(columns["company name"])
                isin = str(row.get(columns.get("isin code", ""), "") or "").strip().upper()
                if normalized_targets is not None and isin not in normalized_targets:
                    non_target_rows_skipped += 1
                    continue
                location = str(row.get(columns.get("location", ""), "") or "").strip()
                market = str(row.get(columns.get("market", ""), "") or "").strip()
                symbol = str(row.get(columns.get("ticker", ""), "") or "").strip()
                dedupe_key = (isin, parsed.isoformat(), symbol, market, location)
                if dedupe_key in seen_candidates:
                    continue
                seen_candidates.add(dedupe_key)

                detail_url = detail_links.get(_norm(name), "")
                detail: dict = {"euronext_showcase_url": page_url}
                if enrich_details and detail_url:
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

            if page_dates and max(page_dates) < start:
                stop_reason = "PAGE_ENTIRELY_BEFORE_START"
                break
        else:
            stop_reason = "MAX_PAGES_REACHED"

        if stop_reason == "MAX_PAGES_REACHED":
            raise RuntimeError(f"EURONEXT_IPO_MAX_PAGES_REACHED:{max_pages}")

        return candidates, {
            "source": source,
            "status": "SUCCESS",
            "count": len(candidates),
            "pages_fetched": pages_fetched,
            "stop_reason": stop_reason,
            "max_pages": max_pages,
            "target_filter_active": normalized_targets is not None,
            "target_isins_requested": len(normalized_targets or set()),
            "non_target_rows_skipped": non_target_rows_skipped,
            "detail_enrichment_enabled": enrich_details,
            "detail_enriched_count": detail_success,
            "detail_failed_count": detail_failed,
            "date_parse_policy": "EURONEXT_DD_MM_YYYY_DAY_FIRST",
            "dedupe_policy": "EXACT_ISIN_DATE_SYMBOL_MARKET_LOCATION",
            "detail_policy": (
                "OFFICIAL_SHOWCASE_DETAIL_ENRICHED"
                if enrich_details
                else "OFFICIAL_PAGINATED_CATALOGUE_EXACT_ISIN_DATE_ONLY"
            ),
            "pagination_policy": "NEWEST_TO_OLDEST_STOP_AFTER_PAGE_BEFORE_START",
        }
    except Exception as exc:
        return [], {
            "source": source,
            "status": "FAILED",
            "count": 0,
            "pages_fetched": pages_fetched,
            "max_pages": max_pages,
            "target_filter_active": normalized_targets is not None,
            "target_isins_requested": len(normalized_targets or set()),
            "non_target_rows_skipped": non_target_rows_skipped,
            "detail_enrichment_enabled": enrich_details,
            "date_parse_policy": "EURONEXT_DD_MM_YYYY_DAY_FIRST",
            "detail": f"{type(exc).__name__}: {str(exc)[:180]}",
            "partial_candidates_discarded": len(candidates),
            "partial_results_published": False,
        }
