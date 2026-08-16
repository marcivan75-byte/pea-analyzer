from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests

from v182.sources import gdelt_news


CURRENCY_PATTERN = r"(?:EUR|€|NOK|SEK|DKK|GBP|£|CHF|USD|\$)"
AMOUNT_PATTERN = r"(?:\d{1,3}(?:[ ,]\d{3})+(?:[.,]\d+)?|\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)"
COMPANY_NEWS_PATH = "/products/equities/company-news/"
LISTVIEW_BASE = "https://live.euronext.com/en/listview/company-press-release/"
PRODUCT_BASE = "https://live.euronext.com/en/product/equities/"
IPO_NEWS_TERMS = (
    "ipo", "listing", "offering", "placement", "prospectus", "information document",
    "admission", "first day", "share capital", "new shares", "retail offering",
)


@dataclass(frozen=True)
class MoneyEvidence:
    amount: float | None
    currency: str
    raw: str


def _norm_currency(value: str) -> str:
    upper = value.upper().strip()
    return {"€": "EUR", "£": "GBP", "$": "USD"}.get(upper, upper)


def _number(value: str) -> float | None:
    text = value.strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(",") == 1 and len(text.split(",")[-1]) <= 2:
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _scaled_money(amount: float | None, suffix: str) -> float | None:
    if amount is None:
        return None
    suffix_norm = suffix.lower().strip()
    if suffix_norm in {"bn", "billion", "billion(s)"}:
        return amount * 1_000_000_000.0
    if suffix_norm in {"m", "mn", "million", "million(s)"}:
        return amount * 1_000_000.0
    if suffix_norm in {"k", "thousand"}:
        return amount * 1_000.0
    return amount


def _money_near(text: str, labels: tuple[str, ...], radius: int = 120) -> MoneyEvidence:
    """Return the monetary expression closest to an explicit semantic label."""
    money_patterns = (
        (re.compile(rf"({CURRENCY_PATTERN})\s*({AMOUNT_PATTERN})\s*(bn|billion|m|mn|million|k|thousand)?", re.I), "currency_first"),
        (re.compile(rf"({AMOUNT_PATTERN})\s*(bn|billion|m|mn|million|k|thousand)?\s*({CURRENCY_PATTERN})", re.I), "amount_first"),
    )
    best: tuple[int, MoneyEvidence] | None = None
    for label in labels:
        for label_match in re.finditer(re.escape(label), text, flags=re.I):
            window_start = max(0, label_match.start() - radius)
            window_end = min(len(text), label_match.end() + radius)
            window = text[window_start:window_end]
            label_start = label_match.start() - window_start
            label_end = label_match.end() - window_start
            for pattern, orientation in money_patterns:
                for money_match in pattern.finditer(window):
                    if orientation == "currency_first":
                        currency_raw = money_match.group(1)
                        amount_raw = money_match.group(2)
                        suffix = money_match.group(3) or ""
                    else:
                        amount_raw = money_match.group(1)
                        suffix = money_match.group(2) or ""
                        currency_raw = money_match.group(3)
                    amount = _scaled_money(_number(amount_raw), suffix)
                    if amount is None:
                        continue
                    if money_match.end() <= label_start:
                        distance = label_start - money_match.end()
                    elif money_match.start() >= label_end:
                        distance = money_match.start() - label_end
                    else:
                        distance = 0
                    raw_start = max(0, min(money_match.start(), label_start) - 20)
                    raw_end = min(len(window), max(money_match.end(), label_end) + 20)
                    evidence = MoneyEvidence(
                        amount,
                        _norm_currency(currency_raw),
                        window[raw_start:raw_end].strip()[:220],
                    )
                    if best is None or distance < best[0]:
                        best = (distance, evidence)
    return best[1] if best is not None else MoneyEvidence(None, "", "")


def _integer_near(text: str, labels: tuple[str, ...]) -> int | None:
    for label in labels:
        match = re.search(rf"({AMOUNT_PATTERN})\s+{re.escape(label)}", text, flags=re.I)
        if not match:
            match = re.search(rf"{re.escape(label)}.{{0,60}}?({AMOUNT_PATTERN})", text, flags=re.I | re.S)
        if match:
            value = _number(match.group(1))
            if value is not None and value >= 0:
                return int(round(value))
    return None


def _document_links(html: str, base_url: str) -> tuple[str, ...]:
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        text = anchor.get_text(" ", strip=True).lower()
        combined = f"{text} {href.lower()}"
        if not href:
            continue
        if any(term in combined for term in ("prospectus", "information document", "admission document", "offering memorandum")):
            absolute = urljoin(base_url, href)
            if absolute not in links:
                links.append(absolute)
    return tuple(links[:8])


def parse_regulated_news(html: str, url: str = "") -> dict:
    soup = BeautifulSoup(html, "lxml")
    text = " ".join(soup.stripped_strings)
    lower = text.lower()
    gross = _money_near(text, ("gross proceeds", "gross proceeds of", "raise total gross proceeds of", "raised a total of"))
    offer_price = _money_near(text, ("offer price", "subscription price", "reference price"))
    cornerstone = _money_near(text, ("cornerstone investor", "cornerstone investment", "pre-committed"))
    new_shares = _integer_near(text, ("new shares", "offer shares"))
    issued_shares = _integer_near(text, ("issued shares", "shares outstanding", "shares in issue"))
    strong_terms = (
        "oversubscribed", "over-subscribed", "significant interest", "strong demand",
        "substantial demand", "multiple times subscribed", "high demand",
    )
    completed_terms = ("successfully completed", "successful completion", "offering has been completed")
    demand = "STRONG_DEMAND" if any(term in lower for term in strong_terms) else (
        "COMPLETED" if any(term in lower for term in completed_terms) else "UNCLASSIFIED"
    )
    documents = _document_links(html, url)
    return {
        "euronext_news_url": url,
        "euronext_news_parse_status": "SUCCESS" if text else "EMPTY",
        "euronext_news_gross_proceeds_local": gross.amount,
        "euronext_news_gross_proceeds_currency": gross.currency,
        "euronext_news_gross_proceeds_evidence": gross.raw,
        "euronext_news_offer_price_local": offer_price.amount,
        "euronext_news_offer_price_currency": offer_price.currency,
        "euronext_news_offer_price_evidence": offer_price.raw,
        "euronext_news_cornerstone_amount_local": cornerstone.amount,
        "euronext_news_cornerstone_currency": cornerstone.currency,
        "euronext_news_cornerstone_evidence": cornerstone.raw,
        "euronext_news_new_shares": new_shares,
        "euronext_news_issued_shares": issued_shares,
        "euronext_news_demand_signal_shadow": demand,
        "euronext_news_primary_offer_detected": any(term in lower for term in ("new shares", "share capital increase", "primary offering")),
        "euronext_news_secondary_offer_detected": any(term in lower for term in ("secondary shares", "sale of existing shares", "selling shareholders")),
        "euronext_news_retail_offer_detected": "retail offering" in lower,
        "euronext_news_management_commitment_detected": any(term in lower for term in ("members of the board and management", "management has pre-committed", "board and management have pre-committed")),
        "euronext_news_cornerstone_detected": "cornerstone investor" in lower,
        "euronext_news_prospectus_reference_detected": "prospectus" in lower,
        "euronext_news_information_document_reference_detected": any(term in lower for term in ("information document", "admission document")),
        "euronext_news_document_urls": "|".join(documents),
    }


def _relevant_company_news_links(html: str, base_url: str, limit: int = 12) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    relevant: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if COMPANY_NEWS_PATH not in href:
            continue
        text = anchor.get_text(" ", strip=True).lower()
        combined = f"{text} {href.lower()}"
        if not any(term in combined for term in IPO_NEWS_TERMS):
            continue
        absolute = urljoin(base_url, href)
        if absolute not in relevant:
            relevant.append(absolute)
    return relevant[:limit]


def _official_listview_urls(isin: object, timeout: int = 15) -> tuple[list[str], str | None, str]:
    isin_text = str(isin or "").strip().upper()
    if len(isin_text) < 8:
        return [], "INVALID_OR_MISSING_ISIN", ""
    listview_url = f"{LISTVIEW_BASE}{isin_text}"
    try:
        response = requests.get(listview_url, headers={"User-Agent": "PEA-Analyzer-IPO-Radar/1.4"}, timeout=timeout)
        response.raise_for_status()
        return _relevant_company_news_links(response.text, listview_url), None, listview_url
    except Exception as exc:
        return [], f"{type(exc).__name__}: {str(exc)[:160]}", listview_url


def _candidate_product_mics(candidate: dict) -> tuple[str, ...]:
    """Return bounded URL candidates only; MIC guesses never affect scoring or PEA eligibility."""
    location = str(candidate.get("euronext_location") or "").strip().upper()
    market = str(candidate.get("exchange") or "").strip().upper()
    if "OSLO" in location or "OSLO" in market:
        return ("MERK", "XOSL")
    if "PARIS" in location or "PARIS" in market:
        if "ACCESS" in market:
            return ("XMLI", "ALXP", "XPAR")
        if "GROWTH" in market:
            return ("ALXP", "XMLI", "XPAR")
        return ("XPAR", "XMLI", "ALXP")
    if "AMSTERDAM" in location or "AMSTERDAM" in market:
        return ("XAMS", "ALXA")
    if "BRUSSELS" in location or "BRUSSELS" in market:
        return ("XBRU", "ALXB")
    if "LISBON" in location or "LISBON" in market:
        return ("XLIS", "ENXL")
    return ()


def _official_product_page_urls(candidate: dict, timeout: int = 15) -> tuple[list[str], str | None, str]:
    isin_text = str(candidate.get("isin") or "").strip().upper()
    if len(isin_text) < 8:
        return [], "INVALID_OR_MISSING_ISIN", ""
    errors: list[str] = []
    first_working_page = ""
    for mic in _candidate_product_mics(candidate):
        product_url = f"{PRODUCT_BASE}{isin_text}-{mic}"
        try:
            response = requests.get(product_url, headers={"User-Agent": "PEA-Analyzer-IPO-Radar/1.4"}, timeout=timeout)
            response.raise_for_status()
            if not first_working_page:
                first_working_page = product_url
            links = _relevant_company_news_links(response.text, product_url)
            if links:
                return links, None, product_url
        except Exception as exc:
            errors.append(f"{mic}:{type(exc).__name__}")
    detail = "|".join(errors[:4]) if errors else None
    return [], detail, first_working_page


def _article_urls(name: object, *, timespan: str = "90d", max_records: int = 30) -> tuple[list[str], str | None]:
    safe = gdelt_news.safe_query_text(name, max_len=100)
    if len(safe) < 3:
        return [], "INVALID_ISSUER_NAME"
    query = f'domain:live.euronext.com "{safe}" (IPO OR listing OR offering OR placement OR prospectus)'
    articles, error = gdelt_news.fetch_articles(query, timespan=timespan, max_records=max_records, timeout=20)
    urls: list[str] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        url = str(article.get("url") or "").strip()
        if "live.euronext.com" not in url.lower() or COMPANY_NEWS_PATH not in url:
            continue
        if url not in urls:
            urls.append(url)
    return urls[:8], error


def enrich_candidate(candidate: dict, timeout: int = 15) -> dict:
    source_text = str(candidate.get("source") or candidate.get("sources") or "").upper()
    if "EURONEXT" not in source_text:
        return candidate

    list_urls, list_error, listview_url = _official_listview_urls(candidate.get("isin"), timeout=timeout)
    product_error = None
    product_url = ""
    fallback_error = None
    if list_urls:
        urls = list_urls
        discovery_status = "DIRECT_SUCCESS"
        discovery_method = "EURONEXT_ISIN_LISTVIEW"
    else:
        product_urls, product_error, product_url = _official_product_page_urls(candidate, timeout=timeout)
        if product_urls:
            urls = product_urls
            discovery_status = "PRODUCT_PAGE_SUCCESS"
            discovery_method = "EURONEXT_PRODUCT_PAGE"
        else:
            fallback_urls, fallback_error = _article_urls(candidate.get("name"))
            urls = fallback_urls
            discovery_status = "GDELT_FALLBACK_SUCCESS" if fallback_urls else (
                "FAILED" if list_error and product_error and fallback_error else "NO_MATCH"
            )
            discovery_method = "GDELT_FALLBACK" if fallback_urls else "NONE"

    candidate["euronext_news_discovery_status"] = discovery_status
    candidate["euronext_news_discovery_method"] = discovery_method
    candidate["euronext_news_listview_url"] = listview_url
    candidate["euronext_news_product_url"] = product_url
    candidate["euronext_news_direct_error"] = list_error or ""
    candidate["euronext_news_product_error"] = product_error or ""
    candidate["euronext_news_discovery_error"] = fallback_error or ""
    candidate["euronext_news_count"] = len(urls)
    candidate["euronext_news_urls"] = "|".join(urls)
    parsed: list[dict] = []
    errors: list[str] = []
    for url in urls:
        try:
            response = requests.get(url, headers={"User-Agent": "PEA-Analyzer-IPO-Radar/1.4"}, timeout=timeout)
            response.raise_for_status()
            parsed.append(parse_regulated_news(response.text, url))
        except Exception as exc:
            errors.append(f"{url}:{type(exc).__name__}:{str(exc)[:80]}")
    candidate["euronext_news_fetch_success_count"] = len(parsed)
    candidate["euronext_news_fetch_errors"] = "|".join(errors[:5])
    if not parsed:
        return candidate

    def first_present(field: str):
        for row in parsed:
            value = row.get(field)
            if value not in (None, "", False):
                return value
        return None

    money_fields = (
        "euronext_news_gross_proceeds_local", "euronext_news_gross_proceeds_currency", "euronext_news_gross_proceeds_evidence",
        "euronext_news_offer_price_local", "euronext_news_offer_price_currency", "euronext_news_offer_price_evidence",
        "euronext_news_cornerstone_amount_local", "euronext_news_cornerstone_currency", "euronext_news_cornerstone_evidence",
        "euronext_news_new_shares", "euronext_news_issued_shares",
    )
    for field in money_fields:
        value = first_present(field)
        if value not in (None, ""):
            candidate[field] = value
    demand_rank = {"UNCLASSIFIED": 0, "COMPLETED": 1, "STRONG_DEMAND": 2}
    candidate["euronext_news_demand_signal_shadow"] = max(
        (str(row.get("euronext_news_demand_signal_shadow") or "UNCLASSIFIED") for row in parsed),
        key=lambda value: demand_rank.get(value, 0),
    )
    for field in (
        "euronext_news_primary_offer_detected", "euronext_news_secondary_offer_detected",
        "euronext_news_retail_offer_detected", "euronext_news_management_commitment_detected",
        "euronext_news_cornerstone_detected", "euronext_news_prospectus_reference_detected",
        "euronext_news_information_document_reference_detected",
    ):
        candidate[field] = any(bool(row.get(field)) for row in parsed)
    docs: list[str] = []
    for row in parsed:
        for value in str(row.get("euronext_news_document_urls") or "").split("|"):
            if value and value not in docs:
                docs.append(value)
    candidate["euronext_news_document_urls"] = "|".join(docs[:12])
    candidate["euronext_news_evidence_policy"] = "SHADOW_FACTS_ONLY_NO_ACTIVE_SCORE_V1.4"
    return candidate
