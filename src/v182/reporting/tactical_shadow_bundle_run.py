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
VERSION = "V21.15.3_TACTICAL_CPU_BUDGET_RUNTIME"
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
    """Cap only V22.1's inner executor while TCT occupies the second model branch.

    Private-repository ubuntu-latest runners have a small CPU budget. V22.1
    historically defaults to four compute workers, and the outer tactical
    overlap adds the TCT branch on top. This wrapper preserves the Action CT
    model and its V22.0 -> V22.1 order while bounding the nested executor.
    """
    original_executor = action_ct_bundle.v221.ThreadPoolExecutor
    cap = max(1, int(worker_cap))

    def capped_executor(*args: Any, **kwargs: Any):
        if args:
            requested = int(args[0])
            args = (min(requested, cap), *args[1:])
        else:
            requested = int(kwargs.get("max_workers", cap))
            kwargs["max_workers"] = min(requested, cap)
        return original_executor(*args, **kwargs)

    action_ct_bundle.v221.ThreadPoolExecutor = capped_executor
    try:
        return action_ct_bundle.run(root=root)
    finally:
        action_ct_bundle.v221.ThreadPoolExecutor = original_executor


def run(root: Path = ROOT) -> dict:
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    original_read_parquet = pd.read_parquet
    original_v221_executor = action_ct_bundle.v221.ThreadPoolExecutor
    parquet_cache = ParquetReadCache(original_read_parquet)
    setattr(pd, "read_parquet", parquet_cache)
    try:
        # Action CT keeps its mandatory internal order V22.0 -> V22.1. The TCT
        # branch is independent from those outputs and can overlap computation.
        # V22.1's nested pool is capped so the 2-vCPU private runner is not
        # flooded by four inner workers plus the independent TCT branch.
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
            action_ct, action_ct_error = action_future.result()
            tct, tct_error = tct_future.result()
    finally:
        setattr(pd, "read_parquet", original_read_parquet)
        action_ct_bundle.v221.ThreadPoolExecutor = original_v221_executor

    errors = [error for error in (action_ct_error, tct_error) if error is not None]
    payload = {
        "status": "SUCCESS_TACTICAL_PARALLEL_SHARED_RUNTIME" if not errors else "TACTICAL_PARALLEL_SHARED_RUNTIME_WITH_STEP_ERRORS",
        "version": VERSION,
        "independent_model_branches_overlapped": True,
        "action_ct_internal_order_preserved": ["ACTION_CT_V22.0", "ACTION_CT_V22.1"],
        "tct_dependency_on_action_ct_outputs": False,
        "shared_parquet_physical_reads_preserved": True,
        "original_pandas_reader_restored": pd.read_parquet is original_read_parquet,
        "original_v22_1_executor_restored": action_ct_bundle.v221.ThreadPoolExecutor is original_v221_executor,
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
    # Keep the historical filename so workflow summaries and downstream audit
    # consumers remain backward compatible while the payload carries V21.15.3.
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
