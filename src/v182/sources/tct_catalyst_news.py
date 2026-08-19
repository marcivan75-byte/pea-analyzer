from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Iterable

from v182.sources.gdelt_news import fetch_articles, lexical_score, safe_query_text
from v182.sources.rate_limit import StartRateLimiter


@dataclass(frozen=True)
class CatalystNews:
    magnitude_score: float | None
    direction_score: float | None
    confidence: float
    article_count: int
    independent_sources: int
    event_types: tuple[str, ...]
    top_headlines: tuple[str, ...]
    window_start_utc: str
    window_end_utc: str
    source: str
    error: str | None = None


def parse_article_timestamp(value: object) -> datetime | None:
    """Parse GDELT/ISO article timestamps and return an aware UTC datetime."""
    text = str(value or "").strip()
    if not text:
        return None
    formats = (
        "%Y%m%dT%H%M%SZ",
        "%Y%m%dT%H%M%S",
        "%Y%m%d%H%M%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _normalise_headline(value: object) -> str:
    return " ".join(str(value or "").lower().split())


def filter_articles_to_window(
    articles: Iterable[dict],
    start_utc: datetime,
    end_utc: datetime,
    *,
    require_timestamp: bool = True,
) -> list[dict]:
    """Strict PIT filter: only articles first seen inside [start, end]."""
    start = start_utc.astimezone(timezone.utc)
    end = end_utc.astimezone(timezone.utc)
    out: list[dict] = []
    seen_headlines: set[str] = set()
    for raw in articles:
        if not isinstance(raw, dict):
            continue
        observed = parse_article_timestamp(raw.get("seendate") or raw.get("seenDate") or raw.get("date"))
        if observed is None:
            if require_timestamp:
                continue
        elif observed < start or observed > end:
            continue
        headline = str(raw.get("title") or "").strip()
        if not headline:
            continue
        key = _normalise_headline(headline)
        if key in seen_headlines:
            continue
        seen_headlines.add(key)
        row = dict(raw)
        row["_observed_utc"] = observed
        out.append(row)
    out.sort(key=lambda row: row.get("_observed_utc") or start, reverse=True)
    return out


_EVENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("PROFIT_WARNING", ("profit warning", "profits warning", "warns on profit", "warning on profit")),
    ("GUIDANCE_CUT", ("cuts guidance", "cut guidance", "lowers guidance", "lowered guidance", "guidance cut", "reduces outlook", "lowered outlook")),
    ("GUIDANCE_RAISED", ("raises guidance", "raised guidance", "lifts guidance", "guidance raised", "raises outlook", "raised outlook")),
    ("EARNINGS_BEAT", ("beats estimates", "beat estimates", "beats expectations", "beat expectations", "earnings beat", "profit beats", "revenue beats")),
    ("EARNINGS_MISS", ("misses estimates", "missed estimates", "misses expectations", "missed expectations", "earnings miss", "profit misses", "revenue misses")),
    ("BANKRUPTCY_DEFAULT", ("bankruptcy", "insolvency", "insolvent", "default on", "debt default")),
    ("FRAUD_INVESTIGATION", ("fraud", "investigation", "investigated", "probe into", "regulatory probe")),
    ("REGULATORY_REJECTION", ("approval rejected", "regulator rejects", "regulatory rejection", "fails approval", "approval denied")),
    ("REGULATORY_APPROVAL", ("regulatory approval", "regulator approves", "approved by", "wins approval", "receives approval")),
    ("MA_ACQUISITION", ("acquisition", "acquire", "acquires", "takeover", "merger", "buyout", "bid for")),
    ("MAJOR_CONTRACT", ("major contract", "wins contract", "awarded contract", "new order", "order worth", "framework agreement")),
    ("CAPITAL_RAISE_DILUTION", ("capital raise", "capital increase", "rights issue", "share offering", "equity offering", "dilution", "new shares")),
    ("DIVIDEND_CUT", ("dividend cut", "cuts dividend", "cut dividend", "suspends dividend", "dividend suspended")),
    ("BUYBACK_DIVIDEND_RAISE", ("share buyback", "stock buyback", "buyback program", "raises dividend", "dividend increase", "special dividend")),
    ("ANALYST_DOWNGRADE", ("analyst downgrade", "downgraded to", "broker downgrade", "rating cut")),
    ("ANALYST_UPGRADE", ("analyst upgrade", "upgraded to", "broker upgrade", "rating raised")),
    ("CEO_DEPARTURE", ("ceo resigns", "ceo resignation", "chief executive resigns", "ceo steps down", "chief executive steps down")),
)


def classify_headline(headline: str, event_weights: dict) -> tuple[str, float, float]:
    text = _normalise_headline(headline)
    for event_type, patterns in _EVENT_PATTERNS:
        if any(pattern in text for pattern in patterns):
            spec = event_weights.get(event_type, {})
            return event_type, float(spec.get("magnitude", 50.0)), float(spec.get("direction", 0.0))
    spec = event_weights.get("OTHER_NEWS", {})
    return "OTHER_NEWS", float(spec.get("magnitude", 35.0)), float(spec.get("direction", 0.0))


def score_windowed_articles(
    articles: list[dict],
    *,
    start_utc: datetime,
    end_utc: datetime,
    cfg: dict,
    error: str | None = None,
) -> CatalystNews:
    news_cfg = cfg["news"]
    filtered = filter_articles_to_window(
        articles,
        start_utc,
        end_utc,
        require_timestamp=bool(news_cfg.get("require_parseable_article_timestamp", True)),
    )
    if not filtered:
        return CatalystNews(
            None, None, 0.0, 0, 0, (), (), start_utc.isoformat(), end_utc.isoformat(), "GDELT_WINDOWED", error
        )

    event_weights = news_cfg["event_weights"]
    scored: list[tuple[float, float, str, str, datetime | None, str]] = []
    domains: set[str] = set()
    for article in filtered:
        headline = str(article.get("title") or "").strip()
        event_type, magnitude, direction = classify_headline(headline, event_weights)
        observed = article.get("_observed_utc")
        domain = str(article.get("domain") or article.get("sourcecountry") or "UNKNOWN").strip().lower()
        if domain and domain != "unknown":
            domains.add(domain)
        scored.append((magnitude, direction, event_type, headline, observed, domain))

    # Highest-impact event anchors magnitude. Corroboration and freshness only
    # modestly lift it; they cannot manufacture a catalyst where none exists.
    strongest = max(item[0] for item in scored)
    independent_sources = len(domains)
    corroboration_full = max(1, int(news_cfg.get("corroboration_full_articles", 3)))
    corroboration = min(independent_sources / corroboration_full, 1.0)

    latest_seen = max((item[4] for item in scored if item[4] is not None), default=None)
    freshness = 0.5
    if latest_seen is not None:
        hours = max(0.0, (end_utc - latest_seen).total_seconds() / 3600.0)
        if hours <= float(news_cfg.get("freshness_hours_full", 4)):
            freshness = 1.0
        elif hours <= float(news_cfg.get("freshness_hours_medium", 12)):
            freshness = 0.75
        else:
            freshness = 0.50
    magnitude = min(100.0, strongest * (0.85 + 0.10 * corroboration + 0.05 * freshness))

    meaningful = [item for item in scored if item[2] != "OTHER_NEWS"]
    if meaningful:
        denominator = sum(max(item[0], 1.0) for item in meaningful)
        direction = sum(item[1] * max(item[0], 1.0) for item in meaningful) / denominator
    else:
        lex = lexical_score([item[3] for item in scored])
        direction = 0.0 if lex.score is None else (float(lex.score) - 50.0) * 2.0
    direction = max(-100.0, min(100.0, direction))

    event_types = tuple(dict.fromkeys(item[2] for item in sorted(scored, key=lambda x: x[0], reverse=True)))
    max_headlines = max(1, int(news_cfg.get("max_headlines_persisted", 5)))
    top_headlines = tuple(item[3] for item in sorted(scored, key=lambda x: (x[0], x[4] or start_utc), reverse=True)[:max_headlines])
    confidence = min(1.0, 0.35 + 0.35 * corroboration + 0.30 * freshness)

    return CatalystNews(
        round(magnitude, 4),
        round(direction, 4),
        round(confidence, 4),
        len(filtered),
        independent_sources,
        event_types,
        top_headlines,
        start_utc.isoformat(),
        end_utc.isoformat(),
        "GDELT_WINDOWED",
        error,
    )


def build_company_query(name: object, cfg: dict) -> str:
    clean = safe_query_text(name, max_len=80)
    return f'"{clean}" {cfg["news"]["candidate_query_suffix"]}' if clean else ""


def fetch_candidate_news(
    candidates: list[dict],
    *,
    start_utc: datetime,
    end_utc: datetime,
    phase: str,
    cfg: dict,
) -> dict[str, CatalystNews]:
    """Fetch a bounded candidate set once per POSTMARKET/PREOPEN snapshot."""
    news_cfg = cfg["news"]
    limit = int(cfg["data_policy"].get("news_query_limit", 60))
    selected = candidates[:limit]
    query_by_isin = {
        str(row.get("isin") or ""): build_company_query(row.get("name"), cfg)
        for row in selected
        if str(row.get("isin") or "") and build_company_query(row.get("name"), cfg)
    }
    if not query_by_isin:
        return {}

    timespan = news_cfg["preopen_fetch_timespan"] if str(phase).upper() == "PREOPEN" else news_cfg["postmarket_fetch_timespan"]
    max_records = int(cfg["data_policy"].get("news_max_records_per_candidate", 20))
    limiter = StartRateLimiter(0.12)
    workers = max(1, min(6, len(query_by_isin)))
    raw_results: dict[str, tuple[list[dict], str | None]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_articles, query, timespan=timespan, max_records=max_records, limiter=limiter): isin
            for isin, query in query_by_isin.items()
        }
        for future in as_completed(futures):
            isin = futures[future]
            try:
                raw_results[isin] = future.result()
            except Exception as exc:  # defensive: fetch_articles normally captures its own failures
                raw_results[isin] = ([], f"{type(exc).__name__}: {str(exc)[:160]}")

    return {
        isin: score_windowed_articles(articles, start_utc=start_utc, end_utc=end_utc, cfg=cfg, error=error)
        for isin, (articles, error) in raw_results.items()
    }
