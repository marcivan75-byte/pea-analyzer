from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
import requests

GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
FINNHUB_EARNINGS = "https://finnhub.io/api/v1/calendar/earnings"
UA = "PEA-Analyzer-V21.1-TCT/1.0"

CATALYST_TERMS: dict[str, tuple[str, int]] = {
    "takeover": ("MNA", 35),
    "acquisition": ("MNA", 25),
    "merger": ("MNA", 25),
    "tender offer": ("MNA", 35),
    "contract": ("CONTRACT", 20),
    "order": ("CONTRACT", 10),
    "award": ("CONTRACT", 20),
    "approval": ("REGULATORY", 20),
    "fda": ("FDA", 30),
    "ema": ("REGULATORY", 25),
    "authorization": ("REGULATORY", 20),
    "guidance raised": ("GUIDANCE", 30),
    "raises guidance": ("GUIDANCE", 30),
    "profit upgrade": ("GUIDANCE", 25),
    "earnings beat": ("EARNINGS", 25),
    "beats estimates": ("EARNINGS", 25),
    "buyback": ("BUYBACK", 20),
    "share repurchase": ("BUYBACK", 20),
}
NEGATIVE_TERMS = {
    "profit warning": 40,
    "guidance cut": 30,
    "cuts guidance": 30,
    "fraud": 35,
    "investigation": 20,
    "bankruptcy": 50,
    "insolvency": 50,
}


def score_headlines(titles: list[str]) -> dict[str, object]:
    if not titles:
        return {"score": None, "positive_hits": 0, "negative_hits": 0, "categories": []}
    positive = 0
    negative = 0
    categories: set[str] = set()
    matched_titles = 0
    for title in titles:
        text = re.sub(r"\s+", " ", str(title or "").lower())
        title_positive = 0
        for term, (category, points) in CATALYST_TERMS.items():
            if term in text:
                title_positive = max(title_positive, points)
                categories.add(category)
        title_negative = max((points for term, points in NEGATIVE_TERMS.items() if term in text), default=0)
        if title_positive:
            positive += title_positive
            matched_titles += 1
        if title_negative:
            negative += title_negative
    novelty = min(20, max(0, matched_titles - 1) * 4)
    score = max(0.0, min(100.0, 25.0 + positive + novelty - negative))
    return {
        "score": round(score, 2),
        "positive_hits": int(positive),
        "negative_hits": int(negative),
        "categories": sorted(categories),
        "matched_titles": int(matched_titles),
    }


def gdelt_discovery(company_name: str, timespan: str = "3d", max_records: int = 35, timeout: int = 10) -> dict[str, object]:
    """Discovery-only source. The result MUST NOT be treated as primary evidence."""
    company = str(company_name or "").strip()
    if not company:
        return {"status": "NO_QUERY", "score": None, "evidence_level": "C_DISCOVERY_ONLY"}
    catalyst_block = (
        '(takeover OR acquisition OR merger OR "tender offer" OR contract OR award OR approval '
        'OR FDA OR EMA OR buyback OR "share repurchase" OR guidance OR earnings)'
    )
    query = f'"{company}" {catalyst_block}'
    response = requests.get(
        GDELT_DOC,
        params={
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": min(max(1, int(max_records)), 75),
            "sort": "HybridRel",
            "timespan": timespan,
        },
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    articles = payload.get("articles", []) if isinstance(payload, dict) else []
    titles = [str(a.get("title") or "") for a in articles if a.get("title")]
    scored = score_headlines(titles)
    return {
        "status": "OK" if titles else "NO_ARTICLES",
        "score": scored["score"],
        "gdelt_catalyst_discovery_score": scored["score"],
        "article_count": len(titles),
        "categories": scored["categories"],
        "query": query,
        "evidence_level": "C_DISCOVERY_ONLY",
        "source": "GDELT_DOC_2",
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def finnhub_earnings_calendar(
    api_key: str,
    from_date: str | None = None,
    to_date: str | None = None,
    international: bool = True,
    timeout: int = 20,
) -> list[dict]:
    start = from_date or date.today().isoformat()
    end = to_date or (date.today() + timedelta(days=30)).isoformat()
    params = {
        "from": start,
        "to": end,
        "international": str(bool(international)).lower(),
        "token": api_key,
    }
    response = requests.get(FINNHUB_EARNINGS, params=params, headers={"User-Agent": UA}, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload.get("earningsCalendar", []) if isinstance(payload, dict) else []
