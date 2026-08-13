from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import re
from urllib.parse import quote_plus
from xml.etree import ElementTree

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
CNN_FEAR_GREED = "https://edition.cnn.com/markets/fear-and-greed"
AAII_SENTIMENT = "https://www.aaii.com/sentimentsurvey"
AAII_INSIGHTS = "https://insights.aaii.com/"


def _requests(requests_module=None):
    if requests_module is not None:
        return requests_module
    import requests
    return requests


def fetch_google_news_context(
    *,
    queries: tuple[str, ...] = ("European stocks", "ECB markets", "oil markets"),
    requests_module=None,
    timeout: int = 20,
) -> tuple[dict, list[dict]]:
    """Best-effort Google News RSS discovery.

    This is discovery/context only. Titles are never converted directly into a
    trading score and a failed RSS fetch does not block the Committee.
    """
    requests_module = _requests(requests_module)
    fields = {}
    failures = []
    total = 0
    latest_titles = []
    for query in queries:
        try:
            response = requests_module.get(
                f"{GOOGLE_NEWS_RSS}?q={quote_plus(query)}&hl=en&gl=US&ceid=US:en",
                timeout=timeout,
                headers={"User-Agent": "PEA-Analyzer/21.6 source-audit"},
            )
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
            items = root.findall(".//item")
            total += len(items)
            for item in items[:3]:
                title = item.findtext("title")
                if title:
                    latest_titles.append(unescape(title).strip())
        except Exception as exc:
            failures.append({"source": "Google News", "query": query, "reason": type(exc).__name__, "detail": str(exc)[:180]})
    if total:
        fields["google_news_discovery_items"] = total
        fields["google_news_latest_titles"] = " | ".join(latest_titles[:6])
        fields["google_news_collected_at"] = datetime.now(timezone.utc).isoformat()
    return fields, failures


def _extract_score_from_cnn_html(text: str) -> tuple[float | None, str | None]:
    # CNN has changed its page representation over time. Prefer JSON fragments
    # explicitly tied to fear_and_greed instead of taking an arbitrary score.
    patterns = (
        r'"fear_and_greed"\s*:\s*\{[^{}]{0,2000}?"score"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'"fearAndGreed"\s*:\s*\{[^{}]{0,2000}?"score"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        r'Fear\s*&amp;\s*Greed[^0-9]{0,200}([0-9]{1,3}(?:\.[0-9]+)?)',
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        try:
            score = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if 0 <= score <= 100:
            label = None
            label_match = re.search(r'"rating"\s*:\s*"([^"]+)"', text[max(0, match.start()-500):match.end()+1000], flags=re.IGNORECASE)
            if label_match:
                label = label_match.group(1)
            return score, label
    return None, None


def fetch_cnn_fear_greed(*, requests_module=None, timeout: int = 20) -> tuple[dict, list[dict]]:
    """Best-effort public-page collector; failure remains explicit N/A.

    CNN does not expose a stable documented public API for this index. The
    collector therefore never silently substitutes another provider.
    """
    requests_module = _requests(requests_module)
    try:
        response = requests_module.get(CNN_FEAR_GREED, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 PEA-Analyzer/21.6"})
        response.raise_for_status()
        score, label = _extract_score_from_cnn_html(response.text)
        if score is None:
            return {}, [{"source": "CNN Fear & Greed", "reason": "PAGE_SCHEMA_UNRESOLVED"}]
        fields = {"cnn_fear_greed_score": score, "cnn_fear_greed_as_of": datetime.now(timezone.utc).date().isoformat()}
        if label:
            fields["cnn_fear_greed_label"] = label
        return fields, []
    except Exception as exc:
        return {}, [{"source": "CNN Fear & Greed", "reason": type(exc).__name__, "detail": str(exc)[:180]}]


def _extract_aaii(text: str) -> dict:
    clean = re.sub(r"\s+", " ", unescape(text))
    fields = {}
    for name, field in (("Bullish", "aaii_bullish_pct"), ("Neutral", "aaii_neutral_pct"), ("Bearish", "aaii_bearish_pct")):
        patterns = (
            rf"{name}(?:\s+sentiment)?[^0-9]{{0,120}}([0-9]{{1,2}}(?:\.[0-9]+)?)%",
            rf"{name}\s*:\s*([0-9]{{1,2}}(?:\.[0-9]+)?)%",
        )
        for pattern in patterns:
            match = re.search(pattern, clean, flags=re.IGNORECASE)
            if match:
                fields[field] = float(match.group(1))
                break
    if "aaii_bullish_pct" in fields and "aaii_bearish_pct" in fields:
        fields["aaii_bull_bear_spread_pct"] = round(fields["aaii_bullish_pct"] - fields["aaii_bearish_pct"], 4)
    return fields


def fetch_aaii_sentiment(*, requests_module=None, timeout: int = 20) -> tuple[dict, list[dict]]:
    requests_module = _requests(requests_module)
    failures = []
    for url in (AAII_SENTIMENT, AAII_INSIGHTS):
        try:
            response = requests_module.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 PEA-Analyzer/21.6"})
            response.raise_for_status()
            fields = _extract_aaii(response.text)
            if len(fields) >= 3:
                fields["aaii_sentiment_as_of"] = datetime.now(timezone.utc).date().isoformat()
                return fields, failures
            failures.append({"source": "AAII", "url": url, "reason": "PAGE_SCHEMA_UNRESOLVED"})
        except Exception as exc:
            failures.append({"source": "AAII", "url": url, "reason": type(exc).__name__, "detail": str(exc)[:180]})
    return {}, failures
