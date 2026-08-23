from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, Callable
import json
import traceback

import pandas as pd

from v182.reporting import action_ct_shadow_bundle_run as action_ct_bundle
from v182.reporting import tct_daily_trader_shadow_run_v24_3_1 as tct_trader


ROOT = Path(__file__).resolve().parents[3]
VERSION = "V21.15.4_TACTICAL_DAG_RUNTIME"
ACTION_CT_V22_1_WORKER_CAP = 2


@dataclass
class ParquetReadCache:
    """Thread-safe cache for plain path-only pandas.read_parquet calls in one process.

    The governed extractors remain untouched. Each logical caller receives a
    deep copy of the raw DataFrame, so model-specific transformations cannot
    leak across Action CT V22.0/V22.1 and TCT V24.3.1.

    A lock covers cache lookup + the physical read + cache insertion. If both
    independent tactical branches request the same parquet concurrently, only
    one physical read occurs. The per-consumer deep copy happens after the lock
    is released so independent model computations are not serialized by copy
    cost. Calls with positional/keyword options are passed through unchanged.
    """

    original_reader: Callable[..., pd.DataFrame]
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    logical_calls: int = 0
    cache_hits: int = 0
    physical_reads: int = 0
    physical_read_seconds: float = 0.0
    passthrough_calls: int = 0
    _lock: RLock = field(default_factory=RLock, repr=False)

    def __call__(self, path: Any, *args: Any, **kwargs: Any) -> pd.DataFrame:
        if args or kwargs or not isinstance(path, (str, Path)):
            with self._lock:
                self.logical_calls += 1
                self.passthrough_calls += 1
            return self.original_reader(path, *args, **kwargs)

        key = str(Path(path).resolve())
        with self._lock:
            self.logical_calls += 1
            cached = self.frames.get(key)
            if cached is None:
                started = perf_counter()
                frame = self.original_reader(path)
                self.physical_read_seconds += perf_counter() - started
                self.physical_reads += 1
                self.frames[key] = frame.copy(deep=True)
                cached = self.frames[key]
            else:
                self.cache_hits += 1
        return cached.copy(deep=True)

    def audit(self) -> dict:
        with self._lock:
            return {
                "logical_read_parquet_calls": int(self.logical_calls),
                "physical_read_parquet_calls": int(self.physical_reads),
                "cache_hits": int(self.cache_hits),
                "passthrough_calls": int(self.passthrough_calls),
                "unique_cached_paths": int(len(self.frames)),
                "physical_read_seconds": round(float(self.physical_read_seconds), 6),
                "raw_consumer_isolation": "DEEP_COPY_PER_READ",
                "thread_safe_cache": True,
                "single_physical_read_per_plain_path": True,
                "consumer_copy_outside_lock": True,
                "non_plain_calls_cached": False,
                "governed_extractors_changed": False,
            }


def _run_step(name: str, runner: Callable[[], dict]) -> tuple[dict, dict | None]:
    try:
        return runner(), None
    except Exception as exc:
        return {}, {
            "step": name,
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": traceback.format_exc(limit=5),
        }


def _run_action_ct_with_worker_cap(root: Path, worker_cap: int = ACTION_CT_V22_1_WORKER_CAP) -> dict:
    """Cap only V22.1's inner executor while TCT occupies the second model branch."""
    original_executor = getattr(action_ct_bundle.v221, "ThreadPoolExecutor")
    cap = max(1, int(worker_cap))

    def capped_executor(*args: Any, **kwargs: Any):
        if args:
            requested = int(args[0])
            args = (min(requested, cap), *args[1:])
        else:
            requested = int(kwargs.get("max_workers", cap))
            kwargs["max_workers"] = min(requested, cap)
        return original_executor(*args, **kwargs)

    setattr(action_ct_bundle.v221, "ThreadPoolExecutor", capped_executor)
    try:
        return action_ct_bundle.run(root=root)
    finally:
        setattr(action_ct_bundle.v221, "ThreadPoolExecutor", original_executor)


def run(
    root: Path = ROOT,
    *,
    tct_complete_callback: Callable[[dict, dict | None], None] | None = None,
) -> dict:
    """Overlap Action CT and TCT; optionally release downstream work at TCT completion.

    The callback is orchestration-only. It receives the completed TCT payload and
    its captured error (if any), cannot change either model, and is invoked before
    waiting for the independent Action CT branch. Existing callers omit it and
    retain the historical behavior/output contract.
    """
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    original_read_parquet = pd.read_parquet
    original_v221_executor = getattr(action_ct_bundle.v221, "ThreadPoolExecutor")
    parquet_cache = ParquetReadCache(original_read_parquet)
    callback_error: dict | None = None
    setattr(pd, "read_parquet", parquet_cache)
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="tactical-model") as pool:
            action_future = pool.submit(
                _run_step,
                "ACTION_CT_V22.0_V22.1",
                lambda: _run_action_ct_with_worker_cap(root),
            )
            tct_future = pool.submit(
                _run_step,
                "TCT_V24.3.1",
                lambda: tct_trader.run(root=root),
            )

            # TCT owns the postmarket seed. Resolve it first so a caller can
            # start network-bound postmarket work while Action CT is still using
            # the second CPU branch. This changes scheduling only, not models.
            tct, tct_error = tct_future.result()
            if tct_complete_callback is not None:
                try:
                    tct_complete_callback(tct, tct_error)
                except Exception as exc:
                    callback_error = {
                        "step": "TCT_COMPLETE_CALLBACK",
                        "type": type(exc).__name__,
                        "message": str(exc)[:500],
                        "traceback": traceback.format_exc(limit=5),
                    }
            action_ct, action_ct_error = action_future.result()
    finally:
        setattr(pd, "read_parquet", original_read_parquet)
        setattr(action_ct_bundle.v221, "ThreadPoolExecutor", original_v221_executor)

    errors = [error for error in (action_ct_error, tct_error, callback_error) if error is not None]
    payload = {
        "status": "SUCCESS_TACTICAL_PARALLEL_SHARED_RUNTIME" if not errors else "TACTICAL_PARALLEL_SHARED_RUNTIME_WITH_STEP_ERRORS",
        "version": VERSION,
        "independent_model_branches_overlapped": True,
        "action_ct_internal_order_preserved": ["ACTION_CT_V22.0", "ACTION_CT_V22.1"],
        "tct_dependency_on_action_ct_outputs": False,
        "tct_completion_released_before_action_ct_join": True,
        "tct_completion_callback_used": tct_complete_callback is not None,
        "shared_parquet_physical_reads_preserved": True,
        "original_pandas_reader_restored": pd.read_parquet is original_read_parquet,
        "original_v22_1_executor_restored": getattr(action_ct_bundle.v221, "ThreadPoolExecutor") is original_v221_executor,
        "action_ct_v22_1_worker_cap": ACTION_CT_V22_1_WORKER_CAP,
        "nested_cpu_oversubscription_reduced": True,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "governed_extractors_changed": False,
        "t1_t2_scope_changed": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
        "external_provider_concurrency_added": False,
        "parquet_runtime": parquet_cache.audit(),
        "steps": {
            "ACTION_CT_V22.0_V22.1": {
                "status": action_ct.get("status"),
                "action_ct_runtime_version": action_ct.get("version"),
            },
            "TCT_V24.3.1": {
                "status": tct.get("status"),
                "rows": tct.get("rows"),
            },
        },
        "errors": errors,
        "total_seconds": round(float(perf_counter() - started), 6),
    }
    audit_path = auditdir / "TACTICAL_SHARED_PARQUET_RUNTIME_V21_13_11.json"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if errors:
        raise RuntimeError(
            "Tactical parallel shared-parquet bundle completed with step error(s): "
            + ", ".join(str(error.get("step")) for error in errors)
        )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))