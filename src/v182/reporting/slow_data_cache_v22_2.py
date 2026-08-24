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
CACHE_VERSION = "V22_2_SLOW_DATA_CACHE_V1"


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


def _cache_dir(root: Path, policy: CachePolicy) -> Path:
    path = root / "state" / "slow_data_cache" / policy.name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _frame_identity(frame: pd.DataFrame) -> str:
    cols = [c for c in ("isin", "provider", "yahoo_ticker") if c in frame.columns]
    if not cols:
        return f"rows={len(frame)}"
    normalized = frame[cols].copy()
    for col in cols:
        normalized[col] = normalized[col].astype(str).fillna("")
    normalized = normalized.sort_values(cols, kind="stable").reset_index(drop=True)
    return normalized.to_csv(index=False, lineterminator="\n")


def _args_fingerprint(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    parts: list[str] = []
    for arg in args:
        if isinstance(arg, pd.DataFrame):
            parts.append("DF:" + _frame_identity(arg))
        elif isinstance(arg, (list, tuple, set)):
            parts.append("SEQ:" + "|".join(sorted(str(x) for x in arg)))
        else:
            parts.append(f"ARG:{type(arg).__name__}:{str(arg)}")
    for key in sorted(kwargs):
        value = kwargs[key]
        if key in {"today", "delay_seconds"}:
            continue
        parts.append(f"KW:{key}:{str(value)}")
    return sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _paths(root: Path, policy: CachePolicy, fingerprint: str) -> tuple[Path, Path]:
    base = _cache_dir(root, policy) / fingerprint
    return base.with_suffix(".pkl"), base.with_suffix(".json")


def _load(root: Path, policy: CachePolicy, fingerprint: str) -> tuple[bool, Any, dict[str, Any]]:
    data_path, meta_path = _paths(root, policy, fingerprint)
    if not data_path.exists() or not meta_path.exists():
        return False, None, {"reason": "MISS_NOT_FOUND"}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        collected = datetime.fromisoformat(str(meta["collected_at_utc"]))
        if collected.tzinfo is None:
            collected = collected.replace(tzinfo=timezone.utc)
        expires = collected + timedelta(days=policy.ttl_days)
        if _utcnow() >= expires:
            return False, None, {**meta, "reason": "MISS_EXPIRED", "expires_at_utc": expires.isoformat()}
        with data_path.open("rb") as handle:
            value = pickle.load(handle)
        return True, value, {**meta, "reason": "HIT", "expires_at_utc": expires.isoformat()}
    except Exception as exc:
        return False, None, {"reason": "MISS_CORRUPT", "detail": f"{type(exc).__name__}:{str(exc)[:180]}"}


def _save(root: Path, policy: CachePolicy, fingerprint: str, value: Any) -> dict[str, Any]:
    data_path, meta_path = _paths(root, policy, fingerprint)
    collected = _utcnow()
    tmp = data_path.with_suffix(".tmp")
    with tmp.open("wb") as handle:
        pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(data_path)
    meta = {
        "version": CACHE_VERSION,
        "policy": policy.name,
        "cadence": policy.cadence,
        "ttl_days": policy.ttl_days,
        "fingerprint": fingerprint,
        "collected_at_utc": collected.isoformat(),
        "next_refresh_utc": (collected + timedelta(days=policy.ttl_days)).isoformat(),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def cached_call(
    original: Callable[..., Any],
    policy: CachePolicy,
    *,
    root: Path = ROOT,
    metrics: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    """Reuse an exact collector result until its governed cadence expires.

    Cache identity follows the instrument/provider worklist, not the run date. Failed
    calls are never cached. The cached value is returned byte-for-byte from pickle,
    preserving collector semantics and evidence metadata.
    """
    def wrapped(*args, **kwargs):
        fingerprint = _args_fingerprint(args, kwargs)
        started = perf_counter()
        hit, value, diag = _load(root, policy, fingerprint)
        if metrics is not None:
            metrics.setdefault(policy.name, {"hits": 0, "misses": 0, "saved": 0, "seconds_saved_estimate": 0.0, "last": {}})
            slot = metrics[policy.name]
            slot["last"] = diag
        if hit:
            if metrics is not None:
                slot["hits"] += 1
            return value
        if metrics is not None:
            slot["misses"] += 1
        value = original(*args, **kwargs)
        meta = _save(root, policy, fingerprint, value)
        if metrics is not None:
            slot["saved"] += 1
            slot["last"] = {**meta, "reason": "MISS_REFRESHED", "refresh_seconds": round(perf_counter() - started, 6)}
        return value
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
        "failed_calls_cached": False,
        "universe_change_invalidates_cache": True,
        "provider_worklist_change_invalidates_cache": True,
        "selection_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "real_orders_enabled": False,
    }
    (audit_dir / "SLOW_DATA_CACHE_V22_2.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
