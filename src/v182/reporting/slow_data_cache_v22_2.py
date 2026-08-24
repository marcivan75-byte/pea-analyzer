from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from time import perf_counter
import json
import pickle
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CACHE_VERSION = "V22_2_SLOW_DATA_CACHE_V2"


@dataclass(frozen=True)
class CachePolicy:
    name: str
    ttl_days: int
    cadence: str


ETF_STRUCTURAL = CachePolicy("etf_structural", 30, "MONTHLY")
ETF_INCEPTION = CachePolicy("etf_inception", 365, "QUASI_STATIC")
ETF_FUND_STRUCTURE = CachePolicy("etf_fund_structure", 30, "MONTHLY")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_key(value: object) -> str:
    text = str(value or "").strip()
    return sha256(text.encode("utf-8")).hexdigest()[:24]


def _cache_dir(root: Path, policy: CachePolicy) -> Path:
    path = root / "state" / "slow_data_cache" / policy.name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _paths(root: Path, policy: CachePolicy, key: str) -> tuple[Path, Path]:
    base = _cache_dir(root, policy) / _safe_key(key)
    return base.with_suffix(".pkl"), base.with_suffix(".json")


def _load_record(root: Path, policy: CachePolicy, key: str) -> tuple[bool, list[dict], dict[str, Any]]:
    data_path, meta_path = _paths(root, policy, key)
    if not data_path.exists() or not meta_path.exists():
        return False, [], {"reason": "MISS_NOT_FOUND", "key": key}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        collected = datetime.fromisoformat(str(meta["collected_at_utc"]))
        if collected.tzinfo is None:
            collected = collected.replace(tzinfo=timezone.utc)
        expires = collected + timedelta(days=policy.ttl_days)
        if _utcnow() >= expires:
            return False, [], {**meta, "reason": "MISS_EXPIRED", "expires_at_utc": expires.isoformat()}
        with data_path.open("rb") as handle:
            observations = pickle.load(handle)
        if not isinstance(observations, list):
            raise TypeError("CACHE_PAYLOAD_NOT_LIST")
        return True, observations, {**meta, "reason": "HIT", "expires_at_utc": expires.isoformat()}
    except Exception as exc:
        return False, [], {"reason": "MISS_CORRUPT", "key": key, "detail": f"{type(exc).__name__}:{str(exc)[:180]}"}


def _save_record(root: Path, policy: CachePolicy, key: str, observations: list[dict]) -> dict[str, Any]:
    data_path, meta_path = _paths(root, policy, key)
    collected = _utcnow()
    tmp = data_path.with_suffix(".tmp")
    with tmp.open("wb") as handle:
        pickle.dump(observations, handle, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(data_path)
    meta = {
        "version": CACHE_VERSION,
        "policy": policy.name,
        "cadence": policy.cadence,
        "ttl_days": policy.ttl_days,
        "instrument_key": key,
        "collected_at_utc": collected.isoformat(),
        "next_refresh_utc": (collected + timedelta(days=policy.ttl_days)).isoformat(),
        "observation_count": len(observations),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _slot(metrics: dict[str, Any] | None, policy: CachePolicy) -> dict[str, Any] | None:
    if metrics is None:
        return None
    return metrics.setdefault(policy.name, {
        "instrument_hits": 0,
        "instrument_misses": 0,
        "instrument_saved": 0,
        "instrument_failures_not_cached": 0,
        "network_instruments_requested": 0,
        "cached_observations_reused": 0,
        "fresh_observations": 0,
        "refresh_seconds": 0.0,
    })


def cached_etf_frame_collector(
    original: Callable[..., tuple[list[dict], list[dict], dict[str, Any]]],
    policy: CachePolicy,
    *,
    root: Path = ROOT,
    metrics: dict[str, Any] | None = None,
) -> Callable[..., tuple[list[dict], list[dict], dict[str, Any]]]:
    """Cache successful ETF collector observations per ISIN and refresh only due ISINs."""
    def wrapped(frame: pd.DataFrame, *args, **kwargs):
        if "isin" not in frame.columns:
            return original(frame, *args, **kwargs)
        slot = _slot(metrics, policy)
        keys = [str(x).strip() for x in frame["isin"].dropna().astype(str).unique() if str(x).strip()]
        cached_obs: list[dict] = []
        due: list[str] = []
        for key in keys:
            hit, observations, _ = _load_record(root, policy, key)
            if hit:
                cached_obs.extend(observations)
                if slot is not None:
                    slot["instrument_hits"] += 1
                    slot["cached_observations_reused"] += len(observations)
            else:
                due.append(key)
                if slot is not None:
                    slot["instrument_misses"] += 1
        if not due:
            collector_metrics = {
                "status": "CACHE_ONLY",
                "requested": 0,
                "cache_hits": len(keys),
                "cache_misses": 0,
                "cadence": policy.cadence,
                "ttl_days": policy.ttl_days,
            }
            return cached_obs, [], collector_metrics

        due_frame = frame[frame["isin"].astype(str).isin(set(due))].copy()
        started = perf_counter()
        fresh_obs, failures, collector_metrics = original(due_frame, *args, **kwargs)
        elapsed = perf_counter() - started
        failure_keys = {str(row.get("isin") or "").strip() for row in failures}
        grouped: dict[str, list[dict]] = {}
        for obs in fresh_obs:
            key = str(obs.get("isin") or "").strip()
            if key:
                grouped.setdefault(key, []).append(obs)
        for key, observations in grouped.items():
            if key in failure_keys:
                if slot is not None:
                    slot["instrument_failures_not_cached"] += 1
                continue
            _save_record(root, policy, key, observations)
            if slot is not None:
                slot["instrument_saved"] += 1
        if slot is not None:
            slot["network_instruments_requested"] += len(due)
            slot["fresh_observations"] += len(fresh_obs)
            slot["refresh_seconds"] = round(float(slot["refresh_seconds"]) + elapsed, 6)
            slot["instrument_failures_not_cached"] += len(failure_keys - set(grouped))
        merged_metrics = dict(collector_metrics or {})
        merged_metrics.update({
            "cache_hits": len(keys) - len(due),
            "cache_misses": len(due),
            "network_requested_after_cache": len(due),
            "cadence": policy.cadence,
            "ttl_days": policy.ttl_days,
        })
        return cached_obs + list(fresh_obs), list(failures), merged_metrics
    return wrapped


def cached_ticker_collector(
    original: Callable[..., tuple[list[dict], list[dict]]],
    policy: CachePolicy,
    *,
    root: Path = ROOT,
    metrics: dict[str, Any] | None = None,
) -> Callable[..., tuple[list[dict], list[dict]]]:
    """Cache successful ticker observations individually; failed tickers always remain due."""
    def wrapped(tickers: list[str], *args, **kwargs):
        slot = _slot(metrics, policy)
        keys = sorted({str(x).strip() for x in tickers if str(x).strip()})
        cached_obs: list[dict] = []
        due: list[str] = []
        for key in keys:
            hit, observations, _ = _load_record(root, policy, key)
            if hit:
                cached_obs.extend(observations)
                if slot is not None:
                    slot["instrument_hits"] += 1
                    slot["cached_observations_reused"] += len(observations)
            else:
                due.append(key)
                if slot is not None:
                    slot["instrument_misses"] += 1
        if not due:
            return cached_obs, []
        started = perf_counter()
        fresh_obs, failures = original(due, *args, **kwargs)
        elapsed = perf_counter() - started
        failure_keys = {str(row.get("ticker") or "").strip() for row in failures}
        grouped: dict[str, list[dict]] = {}
        for obs in fresh_obs:
            key = str(obs.get("ticker") or "").strip()
            if key:
                grouped.setdefault(key, []).append(obs)
        for key, observations in grouped.items():
            if key in failure_keys:
                if slot is not None:
                    slot["instrument_failures_not_cached"] += 1
                continue
            _save_record(root, policy, key, observations)
            if slot is not None:
                slot["instrument_saved"] += 1
        if slot is not None:
            slot["network_instruments_requested"] += len(due)
            slot["fresh_observations"] += len(fresh_obs)
            slot["refresh_seconds"] = round(float(slot["refresh_seconds"]) + elapsed, 6)
            slot["instrument_failures_not_cached"] += len(failure_keys - set(grouped))
        return cached_obs + list(fresh_obs), list(failures)
    return wrapped


def write_audit(root: Path, metrics: dict[str, Any]) -> None:
    audit_dir = root / "outputs" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "generated_at_utc": _utcnow().isoformat(),
        "policies": {
            ETF_STRUCTURAL.name: {"ttl_days": ETF_STRUCTURAL.ttl_days, "cadence": ETF_STRUCTURAL.cadence},
            ETF_INCEPTION.name: {"ttl_days": ETF_INCEPTION.ttl_days, "cadence": ETF_INCEPTION.cadence},
            ETF_FUND_STRUCTURE.name: {"ttl_days": ETF_FUND_STRUCTURE.ttl_days, "cadence": ETF_FUND_STRUCTURE.cadence},
        },
        "metrics": metrics,
        "cache_scope": "PER_INSTRUMENT_SUCCESSFUL_OBSERVATIONS_ONLY",
        "failed_or_partial_instruments_cached": False,
        "new_instruments_refresh_immediately": True,
        "expired_instruments_refresh_immediately": True,
        "selection_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "real_orders_enabled": False,
    }
    (audit_dir / "SLOW_DATA_CACHE_V22_2.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
