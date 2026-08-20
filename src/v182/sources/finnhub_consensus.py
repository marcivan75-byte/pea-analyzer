from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import re

from v182.sources.rate_limit import StartRateLimiter

FINNHUB_BASE = "https://finnhub.io/api/v1"
_SCORE_WEIGHTS = {"strongBuy": 5, "buy": 4, "hold": 3, "sell": 2, "strongSell": 1}
CACHE_VERSION = "FINNHUB_CONSENSUS_CACHE_V1"
_NO_DATA_REASONS = {"NO_RECOMMENDATION_DATA", "EMPTY_RECOMMENDATION_COUNTS"}
_AUTH_DENIED_REASON = "FINNHUB_AUTH_OR_ENTITLEMENT_DENIED"
_SOURCE_BLOCKED_REASON = "FINNHUB_SOURCE_DISABLED_AUTH_OR_ENTITLEMENT"
_SECRET_QUERY_RE = re.compile(r"([?&](?:token|api[_-]?key|apikey|key)=)[^&\s]+", re.IGNORECASE)
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+")


def _sanitize_detail(value: object, limit: int = 160) -> str:
    """Remove credentials from exception/HTTP text before it reaches logs/artifacts."""
    text=str(value or "")
    text=_SECRET_QUERY_RE.sub(r"\1<REDACTED>",text)
    text=_BEARER_RE.sub(r"\1<REDACTED>",text)
    return text[:limit]


def _label_from_score(score: float) -> str:
    if score >= 4.5:
        return "STRONG_BUY"
    if score >= 3.5:
        return "BUY"
    if score >= 2.5:
        return "HOLD"
    if score >= 1.5:
        return "SELL"
    return "STRONG_SELL"


def _counts(row: dict) -> dict[str, int]:
    return {k: int(row.get(k, 0) or 0) for k in _SCORE_WEIGHTS}


def _score_from_counts(counts: dict[str, int]) -> float | None:
    total = sum(counts.values())
    if total <= 0:
        return None
    return sum(counts[k] * w for k, w in _SCORE_WEIGHTS.items()) / total


def _period(row: dict) -> date | None:
    raw = row.get("period")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _previous_monthish(reco: list[dict], latest: dict) -> dict | None:
    latest_date = _period(latest)
    if latest_date is None:
        return reco[1] if len(reco) > 1 else None
    for row in reco[1:]:
        d = _period(row)
        if d is not None and (latest_date - d).days >= 21:
            return row
    return reco[1] if len(reco) > 1 else None


def _fetch_one(ticker: str, api_key: str, requests, limiter: StartRateLimiter) -> tuple[list[dict], list[dict]]:
    failures: list[dict] = []
    try:
        limiter.wait()
        reco_resp = requests.get(
            f"{FINNHUB_BASE}/stock/recommendation",
            params={"symbol": ticker, "token": api_key}, timeout=15,
        )
        if reco_resp.status_code in {401,403}:
            return [], [{
                "ticker":ticker,
                "reason":_AUTH_DENIED_REASON,
                "detail":f"http_status={reco_resp.status_code}; endpoint=stock/recommendation",
            }]
        reco_resp.raise_for_status()
        reco = reco_resp.json()
        if not reco:
            return [], [{"ticker": ticker, "reason": "NO_RECOMMENDATION_DATA"}]

        latest = reco[0]
        counts = _counts(latest)
        score = _score_from_counts(counts)
        if score is None:
            return [], [{"ticker": ticker, "reason": "EMPTY_RECOMMENDATION_COUNTS"}]

        score = round(score, 4)
        rating = _label_from_score(score)
        total = sum(counts.values())
        fields: dict[str, object] = {
            "consensus": rating,
            "consensus_rating": rating,
            "consensus_score": score,
            "consensus_period": latest.get("period"),
            "buy_n": counts["strongBuy"] + counts["buy"],
            "hold_n": counts["hold"],
            "sell_n": counts["strongSell"] + counts["sell"],
            "n_analysts": total,
            "consensus_status": "OK",
            "consensus_source": "Finnhub",
        }

        previous = _previous_monthish(reco, latest)
        if previous is not None:
            previous_counts = _counts(previous)
            previous_score = _score_from_counts(previous_counts)
            if previous_score is not None:
                delta_100 = (score - previous_score) * 20.0
                current_net = (counts["strongBuy"] + counts["buy"]) - (counts["sell"] + counts["strongSell"])
                previous_net = (previous_counts["strongBuy"] + previous_counts["buy"]) - (previous_counts["sell"] + previous_counts["strongSell"])
                fields.update({
                    "consensus_previous_period": previous.get("period"),
                    "consensus_delta_4w": round(delta_100, 4),
                    "net_upgrades_30d": int(current_net - previous_net),
                    "broker_weighted_revision_30d": round(delta_100, 4),
                })

        limiter.wait()
        target_resp = requests.get(
            f"{FINNHUB_BASE}/stock/price-target",
            params={"symbol": ticker, "token": api_key}, timeout=15,
        )
        if target_resp.status_code in {401,403}:
            failures.append({
                "ticker":ticker,
                "reason":"FINNHUB_TARGET_AUTH_OR_ENTITLEMENT_DENIED",
                "detail":f"http_status={target_resp.status_code}; endpoint=stock/price-target",
            })
        elif target_resp.ok:
            target = target_resp.json()
            if target.get("targetMean") is not None:
                fields["target_price"] = target["targetMean"]
            if target.get("lastUpdated"):
                fields["target_last_updated"] = target["lastUpdated"]

        observations = [
            {"ticker": ticker, "field": field, "value": value, "source": "Finnhub"}
            for field, value in fields.items() if value is not None
        ]
        return observations, failures
    except Exception as exc:
        failures.append({"ticker": ticker, "reason": type(exc).__name__, "detail": _sanitize_detail(exc)})
        return [], failures


def fetch_consensus(
    tickers: list[str], api_key: str, delay_seconds: float = 1.1, max_workers: int = 8,
) -> tuple[list[dict], list[dict]]:
    """Collect consensus with a source-auth preflight and bounded concurrency."""
    import requests

    unique = sorted({t for t in tickers if t})
    if not unique:
        return [], []
    limiter = StartRateLimiter(delay_seconds)
    observations: list[dict] = []
    failures: list[dict] = []

    # One preflight prevents thousands of guaranteed-denied calls when the key
    # or plan cannot access the endpoint. Unsupported symbols normally return an
    # empty dataset, not HTTP 401/403, so NO_DATA does not trip this circuit.
    first=unique[0]
    first_obs,first_failures=_fetch_one(first,api_key,requests,limiter)
    observations.extend(first_obs)
    failures.extend(first_failures)
    if any(str(row.get("reason") or "") == _AUTH_DENIED_REASON for row in first_failures):
        failures.append({
            "ticker":"*",
            "reason":_SOURCE_BLOCKED_REASON,
            "detail":f"preflight_ticker={first}; affected_tickers={len(unique)}; further_calls_skipped=true",
        })
        failures.sort(key=lambda row: (str(row.get("ticker", "")), str(row.get("reason", ""))))
        return observations, failures

    remaining=unique[1:]
    workers = max(1, min(int(max_workers), len(remaining))) if remaining else 1
    if remaining:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_fetch_one, ticker, api_key, requests, limiter) for ticker in remaining]
            for future in as_completed(futures):
                obs, failed = future.result()
                observations.extend(obs)
                failures.extend(failed)
    observations.sort(key=lambda row: (str(row.get("ticker", "")), str(row.get("field", ""))))
    failures.sort(key=lambda row: (str(row.get("ticker", "")), str(row.get("reason", ""))))
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


def _entry_observations(entry: dict, ticker: str, cache_state: str) -> list[dict]:
    fetched_at = str(entry.get("fetched_at_utc") or "")
    rows = entry.get("observations") if isinstance(entry.get("observations"), list) else []
    output: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("field") is None or row.get("value") is None:
            continue
        output.append({
            "ticker": ticker,
            "field": row.get("field"),
            "value": row.get("value"),
            "source": "Finnhub",
            "fetched_at_utc": fetched_at,
            "cache_state": cache_state,
        })
    return output


def fetch_consensus_cached(
    tickers: list[str],
    api_key: str,
    cache_path: str | Path,
    *,
    refresh_budget: int = 400,
    max_cache_age_days: float = 10.0,
    negative_cache_days: float = 3.0,
    delay_seconds: float = 1.1,
    max_workers: int = 8,
    now: datetime | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Return full-universe consensus using a timestamp-preserving persistent cache.

    Bootstrap is exhaustive when the source is usable. A source-level 401/403
    trips a fail-fast circuit after one preflight request; current valid cache
    entries may still be emitted, but no stale value is extended or fabricated.
    """
    unique = sorted({str(t).strip() for t in tickers if str(t).strip()})
    path = Path(cache_path)
    current = (now or _now_utc()).astimezone(timezone.utc)
    payload = _load_cache(path)
    entries: dict[str, dict] = payload["entries"]
    if not unique:
        return [], [], {
            "cache_version": CACHE_VERSION, "requested": 0, "live_refresh_requested": 0,
            "live_refresh_success": 0, "cache_hit_tickers": 0, "negative_cache_hits": 0,
        }

    mandatory: list[str] = []
    rotation_candidates: list[tuple[float, str]] = []
    negative_fresh: set[str] = set()
    for ticker in unique:
        entry = entries.get(ticker)
        age = _age_days(entry, current)
        status = str((entry or {}).get("status") or "")
        if entry is None or age > float(max_cache_age_days):
            mandatory.append(ticker)
            continue
        if status == "NO_DATA" and age < float(negative_cache_days):
            negative_fresh.add(ticker)
            continue
        rotation_candidates.append((age, ticker))

    budget = max(0, int(refresh_budget))
    additional_capacity = max(0, budget - len(mandatory))
    rotation_candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = list(dict.fromkeys(mandatory + [ticker for _, ticker in rotation_candidates[:additional_capacity]]))

    live_observations: list[dict] = []
    live_failures: list[dict] = []
    if selected:
        live_observations, live_failures = fetch_consensus(
            selected,
            api_key,
            delay_seconds=delay_seconds,
            max_workers=max_workers,
        )
    source_blocked=any(str(row.get("reason") or "") == _SOURCE_BLOCKED_REASON for row in live_failures)

    obs_by_ticker: dict[str, list[dict]] = {}
    for row in live_observations:
        ticker = str(row.get("ticker") or "")
        if ticker:
            obs_by_ticker.setdefault(ticker, []).append(row)
    failures_by_ticker: dict[str, list[dict]] = {}
    for failure in live_failures:
        ticker = str(failure.get("ticker") or "")
        if ticker and ticker != "*":
            failures_by_ticker.setdefault(ticker, []).append(failure)

    refreshed_at = current.isoformat()
    live_success = 0
    live_no_data = 0
    transient_fallbacks = 0
    expired_after_failure = 0
    skipped_due_source_block=0
    for ticker in selected:
        rows = obs_by_ticker.get(ticker, [])
        ticker_failures = failures_by_ticker.get(ticker, [])
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
        reasons = {str(item.get("reason") or "") for item in ticker_failures}
        if reasons and reasons.issubset(_NO_DATA_REASONS):
            entries[ticker] = {
                "status": "NO_DATA",
                "fetched_at_utc": refreshed_at,
                "observations": [],
                "reason": "|".join(sorted(reasons)),
            }
            live_no_data += 1
            continue
        old_entry = entries.get(ticker)
        if source_blocked:
            skipped_due_source_block += 1
            if old_entry and _age_days(old_entry, current) <= float(max_cache_age_days):
                transient_fallbacks += 1
            elif old_entry:
                entries.pop(ticker, None)
                expired_after_failure += 1
            continue
        if old_entry and _age_days(old_entry, current) <= float(max_cache_age_days):
            transient_fallbacks += 1
            live_failures.append({
                "ticker": ticker,
                "reason": "LIVE_REFRESH_FAILED_CACHE_FALLBACK_USED",
                "detail": f"cached_age_days={_age_days(old_entry, current):.2f}",
            })
        else:
            entries.pop(ticker, None)
            expired_after_failure += 1

    payload["updated_at_utc"] = refreshed_at
    payload["policy"] = {
        "refresh_budget": budget,
        "max_cache_age_days": float(max_cache_age_days),
        "negative_cache_days": float(negative_cache_days),
        "bootstrap_uncached_all": True,
        "stale_after_failure_forbidden": True,
        "auth_or_entitlement_fail_fast":True,
        "secret_redaction_required":True,
    }
    _save_cache(path, payload)

    observations: list[dict] = []
    cache_hit_tickers = 0
    negative_cache_hits = 0
    unusable = 0
    selected_set = set(selected)
    for ticker in unique:
        entry = entries.get(ticker)
        if entry is None:
            unusable += 1
            continue
        age = _age_days(entry, current)
        if age > float(max_cache_age_days):
            unusable += 1
            continue
        status = str(entry.get("status") or "")
        if status == "NO_DATA":
            negative_cache_hits += 1
            continue
        if status != "OK":
            unusable += 1
            continue
        state = "LIVE_REFRESH" if ticker in selected_set and ticker in obs_by_ticker else "CACHE_HIT"
        if state == "CACHE_HIT":
            cache_hit_tickers += 1
        observations.extend(_entry_observations(entry, ticker, state))

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
        "mandatory_refresh_count": len(mandatory),
        "rotation_refresh_count": max(0, len(selected) - len(mandatory)),
        "live_refresh_requested": len(selected),
        "live_refresh_success": live_success,
        "live_no_data": live_no_data,
        "cache_hit_tickers": cache_hit_tickers,
        "negative_cache_hits": negative_cache_hits,
        "transient_cache_fallbacks": transient_fallbacks,
        "expired_after_refresh_failure": expired_after_failure,
        "unusable_tickers": unusable,
        "cache_age_p95_days": p95_age,
        "max_cache_age_days": float(max_cache_age_days),
        "refresh_budget": budget,
        "full_universe_preserved": True,
        "cached_timestamp_preserved": True,
        "source_auth_or_entitlement_blocked":source_blocked,
        "network_calls_skipped_due_source_block":max(0,skipped_due_source_block-1) if source_blocked else 0,
        "secret_redaction_required":True,
    }
    observations.sort(key=lambda row: (str(row.get("ticker", "")), str(row.get("field", ""))))
    live_failures.sort(key=lambda row: (str(row.get("ticker", "")), str(row.get("reason", ""))))
    return observations, live_failures, metrics