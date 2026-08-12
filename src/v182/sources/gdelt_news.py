from __future__ import annotations
from dataclasses import dataclass
import math
import re
import time

GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"

POSITIVE_TERMS = {
    "beat", "beats", "growth", "upgrade", "upgraded", "record", "profit", "profits",
    "surge", "rally", "rebound", "contract", "approval", "approved", "buyback", "dividend",
    "strong", "optimism", "deal", "acquisition", "expansion", "guidance raised", "outperform",
}
NEGATIVE_TERMS = {
    "miss", "misses", "warning", "downgrade", "downgraded", "loss", "losses", "fraud",
    "investigation", "lawsuit", "default", "bankruptcy", "recession", "layoff", "layoffs",
    "plunge", "slump", "cut guidance", "profit warning", "sanction", "war", "tariff", "crisis",
}


@dataclass(frozen=True)
class NewsScore:
    score: float | None
    article_count: int
    positive_hits: int
    negative_hits: int
    source: str


def lexical_score(texts: list[str]) -> NewsScore:
    clean = [str(x).lower() for x in texts if x and str(x).strip()]
    if not clean:
        return NewsScore(None, 0, 0, 0, "GDELT")
    joined = "\n".join(clean)
    pos = sum(joined.count(term) for term in POSITIVE_TERMS)
    neg = sum(joined.count(term) for term in NEGATIVE_TERMS)
    evidence = pos + neg
    if evidence == 0:
        # Articles exist but no directional lexical evidence: keep N/A rather
        # than silently substituting a neutral 50/100.
        return NewsScore(None, len(clean), 0, 0, "GDELT")
    imbalance = (pos - neg) / math.sqrt(evidence)
    score = max(0.0, min(100.0, 50.0 + 12.5 * imbalance))
    return NewsScore(round(score, 4), len(clean), pos, neg, "GDELT")


def fetch_articles(query: str, *, timespan: str = "2d", max_records: int = 50, timeout: int = 20) -> tuple[list[dict], str | None]:
    """Fetch recent articles from the public GDELT DOC API.

    Failure is returned as a typed string; callers decide whether the criterion
    remains missing. No network exception is swallowed silently.
    """
    import requests
    try:
        response = requests.get(
            GDELT_DOC,
            params={
                "query": query,
                "mode": "ArtList",
                "format": "json",
                "maxrecords": max_records,
                "timespan": timespan,
                "sort": "HybridRel",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        articles = payload.get("articles", []) if isinstance(payload, dict) else []
        return articles if isinstance(articles, list) else [], None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {str(exc)[:180]}"


def score_query(query: str, *, timespan: str = "2d", max_records: int = 50) -> tuple[NewsScore, str | None]:
    articles, error = fetch_articles(query, timespan=timespan, max_records=max_records)
    texts=[]
    for article in articles:
        if not isinstance(article, dict):
            continue
        title=article.get("title")
        if title:
            texts.append(str(title))
    return lexical_score(texts), error


def safe_query_text(value: object, max_len: int = 80) -> str:
    text=re.sub(r"[^\w\- .&]", " ", str(value or ""), flags=re.UNICODE)
    return " ".join(text.split())[:max_len]


def pause(seconds: float = 0.12) -> None:
    time.sleep(max(0.0, seconds))
