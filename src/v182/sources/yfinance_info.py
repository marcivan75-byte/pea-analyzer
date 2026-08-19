from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import json
import math

from v182.sources.rate_limit import StartRateLimiter

# Raw yfinance fields are kept under stable V18.2 names. The Committee Master
# resolves them to canonical V21 criteria through explicit semantic aliases.
FIELDS = {
    "marketCap":"market_cap",
    "trailingPE":"per_ttm_yf",
    "forwardPE":"per_forward_yf",
    "priceToBook":"pb",
    "returnOnEquity":"roe_api",
    "returnOnAssets":"roa",
    "debtToEquity":"debt_to_equity",
    "totalDebt":"total_debt_yf",
    "ebitda":"ebitda_yf",
    "freeCashflow":"free_cash_flow",
    "operatingMargins":"marge_ebit",
    "profitMargins":"marge_nette",
    "revenueGrowth":"revenue_growth_yf",
    "earningsGrowth":"earnings_growth_yf",
    "targetMeanPrice":"target_mean_yf",
    "targetHighPrice":"target_high_yf",
    "targetLowPrice":"target_low_yf",
    "currentPrice":"current_price_yf",
    "numberOfAnalystOpinions":"n_analysts_yf",
    "recommendationMean":"recommendation_mean_yf",
    "recommendationKey":"recommendation_key_yf",
    "beta":"beta",
    "dividendYield":"dividend_yield_pct",
    "dividendRate":"dividend_rate_yf",
    "payoutRatio":"payout_ratio",
    "sector":"sector_yf",
    "industry":"industry_yf",
    "country":"country_yf",
    "quoteType":"quote_type_yf",
    "earningsTimestamp":"earnings_timestamp_yf",
    "earningsTimestampStart":"earnings_timestamp_start_yf",
    "earningsTimestampEnd":"earnings_timestamp_end_yf",
}

CACHE_VERSION = "YFINANCE_INFO_CACHE_V1"
_DERIVED_EARNINGS_FIELDS = {"days_to_earnings", "earnings_within_7d_flag", "earnings_within_30d_flag"}
_CACHE_HIT_DROP_FIELDS = {"current_price_yf"} | _DERIVED_EARNINGS_FIELDS


def _future_earnings_fields(info: dict, now_ts: float | None = None) -> dict:
    """Expose objective earnings-calendar fields without inventing catalyst score."""
    now=now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    candidates=[]
    for key in ("earningsTimestampStart","earningsTimestamp","earningsTimestampEnd"):
        value=info.get(key)
        try:
            ts=float(value)
        except (TypeError,ValueError):
            continue
        if ts >= now - 86400:  # tolerate same-day/time-zone publication windows
            candidates.append(ts)
    if not candidates:
        return {}
    next_ts=min(candidates)
    days=(next_ts-now)/86400.0
    return {
        "days_to_earnings":round(days,3),
        "earnings_within_7d_flag":1.0 if 0 <= days <= 7 else 0.0,
        "earnings_within_30d_flag":1.0 if 0 <= days <= 30 else 0.0,
        "next_earnings_timestamp_yf":int(next_ts),
    }


def _collect_one(ticker: str, yf, limiter: StartRateLimiter) -> tuple[list[dict], dict | None]:
    limiter.wait()
    try:
        info = yf.Ticker(ticker).get_info()
        observations=[]
        for source_field, target_field in FIELDS.items():
            value = info.get(source_field)
            if value is not None:
                observations.append({"ticker":ticker,"field":target_field,"value":value,"source":"yfinance"})
        for field,value in _future_earnings_fields(info).items():
            observations.append({"ticker":ticker,"field":field,"value":value,"source":"yfinance"})
        return observations, None
    except Exception as exc:
        return [], {"ticker": ticker, "error": type(exc).__name__, "detail": str(exc)[:160]}


def collect_info(
    tickers: list[str], delay_seconds: float = 0.4, max_workers: int = 4,
) -> tuple[list[dict], list[dict]]:
    """Collect yfinance metadata with bounded concurrency and stable rate cadence.

    `delay_seconds` is the minimum interval between request starts globally.
    Network waits may overlap across a small worker pool, but the source
    request-start cadence is not raised.
    """
    import yfinance as yf

    unique=sorted({x for x in tickers if x})
    if not unique:
        return [], []
    limiter=StartRateLimiter(delay_seconds)
    observations: list[dict]=[]
    failures: list[dict]=[]
    workers=max(1,min(int(max_workers),len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures=[executor.submit(_collect_one,ticker,yf,limiter) for ticker in unique]
        for future in as_completed(futures):
            obs,failure=future.result()
            observations.extend(obs)
            if failure is not None:
                failures.append(failure)
    observations.sort(key=lambda row:(str(row.get("ticker","")),str(row.get("field",""))))
    failures.sort(key=lambda row:str(row.get("ticker","")))
    return observations, failures


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(entry: dict | None, now: datetime) -> float:
    if not entry:
        return math.inf
    fetched = _parse_utc(entry.get("fetched_at_utc"))
    if fetched is None:
        return math.inf
    return max(0.0, (now - fetched).total_seconds() / 86400.0)


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {"version": CACHE_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": CACHE_VERSION, "entries": {}}
    if payload.get("version") != CACHE_VERSION or not isinstance(payload.get("entries"), dict):
        return {"version": CACHE_VERSION, "entries": {}}
    return payload


def _save_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def _cached_earnings_observations(entry: dict, ticker: str, now: datetime) -> list[dict]:
    rows = entry.get("observations") if isinstance(entry.get("observations"), list) else []
    timestamp = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("field") == "next_earnings_timestamp_yf":
            try:
                timestamp = float(row.get("value"))
            except (TypeError, ValueError):
                timestamp = None
            break
    if timestamp is None:
        return []
    days = (timestamp - now.timestamp()) / 86400.0
    if days < -1.0:
        return []
    return [
        {"ticker": ticker, "field": "days_to_earnings", "value": round(days, 3), "source": "yfinance_CACHE", "cache_state": "CACHE_HIT"},
        {"ticker": ticker, "field": "earnings_within_7d_flag", "value": 1.0 if 0 <= days <= 7 else 0.0, "source": "yfinance_CACHE", "cache_state": "CACHE_HIT"},
        {"ticker": ticker, "field": "earnings_within_30d_flag", "value": 1.0 if 0 <= days <= 30 else 0.0, "source": "yfinance_CACHE", "cache_state": "CACHE_HIT"},
    ]


def _entry_observations(entry: dict, ticker: str, cache_state: str, now: datetime) -> list[dict]:
    fetched_at = str(entry.get("fetched_at_utc") or "")
    rows = entry.get("observations") if isinstance(entry.get("observations"), list) else []
    output: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("field") is None or row.get("value") is None:
            continue
        field = str(row.get("field"))
        if cache_state == "CACHE_HIT" and field in _CACHE_HIT_DROP_FIELDS:
            continue
        output.append({
            "ticker": ticker,
            "field": field,
            "value": row.get("value"),
            "source": "yfinance" if cache_state == "LIVE_REFRESH" else "yfinance_CACHE",
            "fetched_at_utc": fetched_at,
            "cache_state": cache_state,
        })
    if cache_state == "CACHE_HIT":
        output.extend(_cached_earnings_observations(entry, ticker, now))
    return output


def collect_info_cached(
    tickers: list[str],
    cache_path: str | Path,
    *,
    max_cache_age_days: float = 7.0,
    delay_seconds: float = 0.4,
    max_workers: int = 4,
    now: datetime | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Collect yfinance info with a persistent TTL cache.

    Uncached or expired tickers are refreshed exhaustively. Fresh cache entries
    avoid repeat API calls. Expired data is never emitted after a failed refresh.
    `current_price_yf` is intentionally not replayed from cache because OHLCV
    provides the fresher market price used by Committee scoring. Earnings-window
    fields are recomputed from the cached timestamp at read time so they do not
    become stale merely because the metadata request itself is cached.
    """
    unique = sorted({str(t).strip() for t in tickers if str(t).strip()})
    path = Path(cache_path)
    current = (now or _now_utc()).astimezone(timezone.utc)
    payload = _load_cache(path)
    entries: dict[str, dict] = payload["entries"]
    max_age = max(0.0, float(max_cache_age_days))

    refresh = [ticker for ticker in unique if _age_days(entries.get(ticker), current) > max_age]
    live_observations: list[dict] = []
    live_failures: list[dict] = []
    if refresh:
        live_observations, live_failures = collect_info(
            refresh,
            delay_seconds=delay_seconds,
            max_workers=max_workers,
        )

    obs_by_ticker: dict[str, list[dict]] = {}
    for row in live_observations:
        ticker = str(row.get("ticker") or "")
        if ticker:
            obs_by_ticker.setdefault(ticker, []).append(row)
    failure_by_ticker = {str(row.get("ticker") or ""): row for row in live_failures if row.get("ticker")}

    refreshed_at = current.isoformat()
    live_success = 0
    live_no_data = 0
    expired_after_failure = 0
    for ticker in refresh:
        rows = obs_by_ticker.get(ticker, [])
        if rows:
            entries[ticker] = {
                "status": "OK",
                "fetched_at_utc": refreshed_at,
                "observations": [
                    {"field": row.get("field"), "value": row.get("value")}
                    for row in rows if row.get("field") is not None and row.get("value") is not None
                ],
            }
            live_success += 1
            continue
        if ticker in failure_by_ticker:
            entries.pop(ticker, None)
            expired_after_failure += 1
            continue
        entries[ticker] = {
            "status": "NO_DATA",
            "fetched_at_utc": refreshed_at,
            "observations": [],
        }
        live_no_data += 1

    payload["updated_at_utc"] = refreshed_at
    payload["policy"] = {
        "max_cache_age_days": max_age,
        "bootstrap_uncached_all": True,
        "expired_after_failure_forbidden": True,
        "cache_hit_current_price_replay": False,
        "earnings_window_recomputed_on_cache_hit": True,
    }
    _save_cache(path, payload)

    observations: list[dict] = []
    cache_hit_tickers = 0
    negative_cache_hits = 0
    unusable = 0
    refresh_set = set(refresh)
    for ticker in unique:
        entry = entries.get(ticker)
        if entry is None or _age_days(entry, current) > max_age:
            unusable += 1
            continue
        status = str(entry.get("status") or "")
        if status == "NO_DATA":
            negative_cache_hits += 1
            continue
        if status != "OK":
            unusable += 1
            continue
        state = "LIVE_REFRESH" if ticker in refresh_set and ticker in obs_by_ticker else "CACHE_HIT"
        if state == "CACHE_HIT":
            cache_hit_tickers += 1
        observations.extend(_entry_observations(entry, ticker, state, current))

    ages = sorted(
        _age_days(entries.get(ticker), current)
        for ticker in unique
        if entries.get(ticker) and math.isfinite(_age_days(entries.get(ticker), current))
    )
    p95_age = None
    if ages:
        index = min(len(ages) - 1, max(0, math.ceil(len(ages) * 0.95) - 1))
        p95_age = round(float(ages[index]), 3)
    metrics = {
        "cache_version": CACHE_VERSION,
        "requested": len(unique),
        "cache_entries": len(entries),
        "live_refresh_requested": len(refresh),
        "live_refresh_success": live_success,
        "live_no_data": live_no_data,
        "cache_hit_tickers": cache_hit_tickers,
        "negative_cache_hits": negative_cache_hits,
        "unusable_tickers": unusable,
        "expired_after_failure": expired_after_failure,
        "cache_age_p95_days": p95_age,
        "max_cache_age_days": max_age,
    }
    return observations, live_failures, metrics
