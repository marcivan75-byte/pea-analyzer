from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests

from v182.sources import gdelt_news


CURRENCY_PATTERN = r"(?:EUR|€|NOK|SEK|DKK|GBP|£|CHF|USD|\$)"
AMOUNT_PATTERN = r"\d{1,3}(?:[ ,.]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
COMPANY_NEWS_PATH = "/products/equities/company-news/"


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
    """Return the monetary expression closest to an explicit semantic label.

    Long Euronext sentences often contain both an offer price and total proceeds.
    Selecting the first money token in a broad regex can therefore bind NOK 7/share
    to 'gross proceeds'. We instead rank currency/amount expressions by character
    distance to each target label inside a bounded local window.
    """
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
    urls, discovery_error = _article_urls(candidate.get("name"))
    candidate["euronext_news_discovery_status"] = "FAILED" if discovery_error and not urls else ("SUCCESS" if urls else "NO_MATCH")
    candidate["euronext_news_discovery_error"] = discovery_error or ""
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
