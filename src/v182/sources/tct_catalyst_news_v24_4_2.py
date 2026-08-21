from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from statistics import median
from time import monotonic
import json
import math

from v182.sources import tct_catalyst_news as base
from v182.sources.gdelt_news import fetch_articles, lexical_score
from v182.sources.rate_limit import StartRateLimiter

VERSION = "TCT_V24.4.2_CATALYST_NEWS"


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
    match_confidence: float | None = None
    cache_hit: bool = False


class NewsBatch(dict):
    def __init__(self, *args, metrics: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.metrics = metrics or {}


def _normalise(value: object) -> str:
    return base._normalise_headline(value)


def _catalog(cfg: dict) -> dict[str, tuple[str, ...]]:
    raw = cfg.get("news", {}).get("event_patterns", {})
    if isinstance(raw, dict) and raw:
        return {
            str(event): tuple(_normalise(p) for p in patterns if str(p).strip())
            for event, patterns in raw.items()
            if isinstance(patterns, list)
        }
    return {event: tuple(patterns) for event, patterns in base._EVENT_PATTERNS}


def classify_headline(headline: str, cfg: dict) -> tuple[str, float, float, float]:
    """Classify with negation protection and specificity-first precedence."""
    text = _normalise(headline)
    negated = any(_normalise(p) in text for p in cfg.get("news", {}).get("negation_patterns", []))
    best = None
    for event_type, patterns in _catalog(cfg).items():
        matches = [p for p in patterns if p and p in text]
        if not matches:
            continue
        if event_type == "FRAUD_INVESTIGATION" and negated:
            continue
        spec = cfg["news"]["event_weights"].get(event_type, {})
        magnitude = float(spec.get("magnitude", 50.0))
        direction = float(spec.get("direction", 0.0))
        first = min(text.find(p) for p in matches)
        confidence = min(1.0, 0.55 + 0.12 * (len(set(matches)) - 1) + (0.10 if first <= 80 else 0.0))
        specificity = max(len(p) for p in matches)
        candidate = (specificity, magnitude * confidence, event_type, magnitude, direction, confidence)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is not None:
        _, _, event_type, magnitude, direction, confidence = best
        return event_type, magnitude, direction, confidence
    spec = cfg["news"]["event_weights"].get("OTHER_NEWS", {})
    return "OTHER_NEWS", float(spec.get("magnitude", 35.0)), float(spec.get("direction", 0.0)), 0.35


def score_windowed_articles(articles: list[dict], *, start_utc: datetime, end_utc: datetime, cfg: dict, error: str | None = None, cache_hit: bool = False) -> CatalystNews:
    filtered = base.filter_articles_to_window(
        articles,
        start_utc,
        end_utc,
        require_timestamp=bool(cfg["news"].get("require_parseable_article_timestamp", True)),
    )
    if not filtered:
        if error:
            return CatalystNews(None, None, 0.0, 0, 0, (), (), start_utc.isoformat(), end_utc.isoformat(), "GDELT", error, None, cache_hit)
        return CatalystNews(0.0, 0.0, 1.0, 0, 0, (), (), start_utc.isoformat(), end_utc.isoformat(), "GDELT", None, 1.0, cache_hit)

    scored = []
    domains: set[str] = set()
    for article in filtered:
        headline = str(article.get("title") or "").strip()
        event_type, magnitude, direction, match_conf = classify_headline(headline, cfg)
        observed = article.get("_observed_utc")
        domain = str(article.get("domain") or article.get("sourcecountry") or "UNKNOWN").strip().lower()
        if domain and domain != "unknown":
            domains.add(domain)
        scored.append((magnitude, direction, event_type, headline, observed, match_conf))

    corroboration = min(len(domains) / max(1, int(cfg["news"].get("corroboration_full_articles", 3))), 1.0)
    latest = max((x[4] for x in scored if x[4] is not None), default=None)
    freshness = 0.5
    if latest is not None:
        hours = max(0.0, (end_utc - latest).total_seconds() / 3600.0)
        if hours <= float(cfg["news"].get("freshness_hours_full", 4)):
            freshness = 1.0
        elif hours <= float(cfg["news"].get("freshness_hours_medium", 12)):
            freshness = 0.75
    strongest = max(x[0] * x[5] for x in scored)
    magnitude = min(100.0, strongest * (0.88 + 0.08 * corroboration + 0.04 * freshness))
    min_conf = float(cfg["news"].get("minimum_match_confidence_for_direction", 0.55))
    meaningful = [x for x in scored if x[2] != "OTHER_NEWS" and x[5] >= min_conf]
    if meaningful:
        denominator = sum(max(x[0] * x[5], 1.0) for x in meaningful)
        direction = sum(x[1] * max(x[0] * x[5], 1.0) for x in meaningful) / denominator
    else:
        lex = lexical_score([x[3] for x in scored])
        direction = 0.0 if lex.score is None else (float(lex.score) - 50.0) * 2.0
    direction = max(-100.0, min(100.0, direction))
    ordered = sorted(scored, key=lambda x: (x[0] * x[5], x[4] or start_utc), reverse=True)
    event_types = tuple(dict.fromkeys(x[2] for x in ordered))
    top_headlines = tuple(x[3] for x in ordered[: max(1, int(cfg["news"].get("max_headlines_persisted", 5)))])
    match_conf = sum(x[5] for x in scored) / len(scored)
    confidence = min(1.0, 0.25 + 0.25 * corroboration + 0.20 * freshness + 0.30 * match_conf)
    return CatalystNews(round(magnitude, 4), round(direction, 4), round(confidence, 4), len(filtered), len(domains), event_types, top_headlines, start_utc.isoformat(), end_utc.isoformat(), "GDELT_WINDOWED_V24_4_2", error, round(match_conf, 4), cache_hit)


def _cache_key(query: str, start_utc: datetime, end_utc: datetime) -> str:
    return sha256(f"{query}|{start_utc.isoformat()}|{end_utc.isoformat()}".encode()).hexdigest()


def _load_cache(root: Path, cfg: dict) -> dict:
    spec = cfg.get("news", {}).get("cache", {})
    if not bool(spec.get("enabled", True)):
        return {}
    path = root / str(spec.get("path", "state/tct_context/TCT_V24_4_2_NEWS_CACHE.json"))
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_cache(root: Path, cfg: dict, cache: dict) -> None:
    spec = cfg.get("news", {}).get("cache", {})
    if not bool(spec.get("enabled", True)):
        return
    path = root / str(spec.get("path", "state/tct_context/TCT_V24_4_2_NEWS_CACHE.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _cached(cache: dict, key: str, ttl: float):
    item = cache.get(key)
    if not isinstance(item, dict) or item.get("error"):
        return None
    try:
        saved = datetime.fromisoformat(str(item["saved_at_utc"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    if saved.tzinfo is None:
        saved = saved.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - saved.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > ttl or not isinstance(item.get("articles"), list):
        return None
    return item["articles"], None


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(math.ceil(0.95 * len(ordered))) - 1))
    return round(float(ordered[idx]), 4)


def fetch_candidate_news(candidates: list[dict], *, start_utc: datetime, end_utc: datetime, phase: str, cfg: dict, budget_seconds: float | None = None, root: Path | None = None) -> NewsBatch:
    root = root or Path(__file__).resolve().parents[3]
    query_by_isin = {}
    for row in candidates[: int(cfg["data_policy"].get("news_query_limit", 60))]:
        isin = str(row.get("isin") or "")
        query = base.build_company_query(row.get("name"), cfg)
        if isin and query:
            query_by_isin[isin] = query
    if not query_by_isin:
        return NewsBatch(metrics={"requested": 0, "completed": 0})

    parallel = cfg["news"].get("parallelism", {})
    workers = max(1, min(int(parallel.get("workers", 6)), int(parallel.get("workers_max", 8)), len(query_by_isin)))
    wave_size = max(1, int(parallel.get("wave_size", 15)))
    delay = float(parallel.get("start_delay_seconds", 0.12))
    limiter = StartRateLimiter(delay)
    ttl = float(cfg["news"].get("cache", {}).get("ttl_seconds", 7200))
    timespan = cfg["news"]["preopen_fetch_timespan"] if str(phase).upper() == "PREOPEN" else cfg["news"]["postmarket_fetch_timespan"]
    max_records = int(cfg["data_policy"].get("news_max_records_per_candidate", 25))
    timeout = int(cfg["news"].get("request_timeout_seconds", 12))
    cache = _load_cache(root, cfg)
    results = {}
    latencies: list[float] = []
    cache_hits = 0
    breaker = False
    started = monotonic()
    items = list(query_by_isin.items())

    for offset in range(0, len(items), wave_size):
        if budget_seconds is not None and monotonic() - started >= float(budget_seconds):
            breaker = True
            break
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for isin, query in items[offset : offset + wave_size]:
                key = _cache_key(query, start_utc, end_utc)
                hit = _cached(cache, key, ttl)
                if hit is not None:
                    results[isin] = (*hit, True)
                    cache_hits += 1
                    continue
                request_started = monotonic()
                future = executor.submit(fetch_articles, query, timespan=timespan, max_records=max_records, timeout=timeout, limiter=limiter)
                futures[future] = (isin, key, request_started)
            for future in as_completed(futures):
                isin, key, request_started = futures[future]
                try:
                    articles, error = future.result()
                except Exception as exc:
                    articles, error = [], f"{type(exc).__name__}: {str(exc)[:160]}"
                latencies.append(monotonic() - request_started)
                articles = [dict(x) for x in articles if isinstance(x, dict)]
                if error is None:
                    cache[key] = {"saved_at_utc": datetime.now(timezone.utc).isoformat(), "articles": articles, "error": None}
                results[isin] = (articles, error, False)

    if breaker:
        for isin in query_by_isin:
            results.setdefault(isin, ([], "NEWS_BUDGET_CIRCUIT_BREAKER", False))
    _save_cache(root, cfg, cache)
    batch = NewsBatch()
    for isin, (articles, error, cache_hit) in results.items():
        batch[isin] = score_windowed_articles(articles, start_utc=start_utc, end_utc=end_utc, cfg=cfg, error=error, cache_hit=cache_hit)
    errors = sum(1 for item in batch.values() if item.error)
    batch.metrics = {
        "requested": len(query_by_isin),
        "completed": len(batch),
        "final_error_rate": None if not batch else round(errors / len(batch), 4),
        "secondary_source_configured": bool(cfg["news"].get("secondary_source_enabled", False)),
        "secondary_source_status": "CONTRACT_PRESENT_NOT_ACTIVATED_UNTIL_PROVIDER_VALIDATION",
        "cache_hits": cache_hits,
        "circuit_breaker_triggered": breaker,
        "workers": workers,
        "latency_p50_seconds": None if not latencies else round(float(median(latencies)), 4),
        "latency_p95_seconds": _p95(latencies),
        "elapsed_seconds": round(monotonic() - started, 4),
        "budget_seconds": budget_seconds,
    }
    return batch
