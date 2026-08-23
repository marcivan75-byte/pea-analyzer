from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
import math
import re
import threading
import time

import requests

from v182.sources.rate_limit import StartRateLimiter

GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MIN_START_INTERVAL_SECONDS = 1.0
GDELT_RETRY_BACKOFF_SECONDS = (2.0, 5.0)
_GDELT_GLOBAL_LIMITER = StartRateLimiter(GDELT_MIN_START_INTERVAL_SECONDS)
_GDELT_HTTP_LOCAL = threading.local()

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
        return NewsScore(None, len(clean), 0, 0, "GDELT")
    imbalance = (pos - neg) / math.sqrt(evidence)
    score = max(0.0, min(100.0, 50.0 + 12.5 * imbalance))
    return NewsScore(round(score, 4), len(clean), pos, neg, "GDELT")


def _retryable_gdelt_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return (
        "429" in text
        or type(exc).__name__ in {"ReadTimeout", "ConnectTimeout", "ConnectionError"}
    )


def _http_get(url: str, **kwargs):
    """Reuse one requests.Session per worker without sharing sessions across threads."""
    session = getattr(_GDELT_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        _GDELT_HTTP_LOCAL.session = session
    return session.get(url, **kwargs)


def fetch_articles(
    query: str,
    *,
    timespan: str = "2d",
    max_records: int = 50,
    timeout: int = 20,
    limiter: StartRateLimiter | None = None,
) -> tuple[list[dict], str | None]:
    """Fetch recent GDELT articles with provider-safe cadence and bounded retries.

    GDELT is a shared public endpoint and can return HTTP 429 when callers start
    requests too aggressively.  A process-wide limiter enforces a conservative
    one-request-per-second start cadence even when callers use several workers.
    Transient 429/time-out/connection failures receive two bounded retries.  All
    final failures remain explicit and are never imputed. HTTP connections are
    reused per worker; query set, request cadence and freshness are unchanged.
    """
    last_error: str | None = None
    for attempt in range(len(GDELT_RETRY_BACKOFF_SECONDS) + 1):
        try:
            if limiter is not None:
                limiter.wait()
            _GDELT_GLOBAL_LIMITER.wait()
            response = _http_get(
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
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            if attempt >= len(GDELT_RETRY_BACKOFF_SECONDS) or not _retryable_gdelt_error(exc):
                return [], last_error
            time.sleep(GDELT_RETRY_BACKOFF_SECONDS[attempt])
    return [], last_error or "GDELT_UNKNOWN_ERROR"


def _score_query_uncached(
    query: str,
    timespan: str,
    max_records: int,
    limiter: StartRateLimiter | None = None,
) -> tuple[NewsScore, str | None]:
    articles, error = fetch_articles(
        query,
        timespan=timespan,
        max_records=max_records,
        limiter=limiter,
    )
    texts=[]
    for article in articles:
        if not isinstance(article, dict):
            continue
        title=article.get("title")
        if title:
            texts.append(str(title))
    return lexical_score(texts), error


@lru_cache(maxsize=2048)
def _score_query_cached(
    query: str,
    timespan: str,
    max_records: int,
) -> tuple[NewsScore, str | None]:
    """Exact intra-process cache; a new GitHub run starts with an empty cache."""
    return _score_query_uncached(query,timespan,max_records)


def score_query(
    query: str,
    *,
    timespan: str = "2d",
    max_records: int = 50,
) -> tuple[NewsScore, str | None]:
    return _score_query_cached(str(query), str(timespan), int(max_records))


def score_queries(
    queries: list[str],
    *,
    timespan: str = "2d",
    max_records: int = 50,
    delay_seconds: float = 0.12,
    max_workers: int = 6,
) -> dict[str, tuple[NewsScore, str | None]]:
    """Score each exact unique query once with bounded concurrent I/O.

    The logical query set, 2-day window, record limit and lexical formula remain
    unchanged. Only exact duplicates are removed; request starts stay rate-limited.
    """
    unique = sorted({str(q) for q in queries if str(q).strip()})
    if not unique:
        return {}
    limiter = StartRateLimiter(delay_seconds)
    workers = max(1, min(int(max_workers), len(unique)))
    out: dict[str, tuple[NewsScore, str | None]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _score_query_uncached,
                query,
                str(timespan),
                int(max_records),
                limiter,
            ): query
            for query in unique
        }
        for future in as_completed(futures):
            out[futures[future]] = future.result()
    return out


def safe_query_text(value: object, max_len: int = 80) -> str:
    text=re.sub(r"[^\w\- .&]", " ", str(value or ""), flags=re.UNICODE)
    return " ".join(text.split())[:max_len]


def pause(seconds: float = 0.12) -> None:
    """Backward-compatible helper for callers not yet using score_queries."""
    time.sleep(max(0.0, seconds))