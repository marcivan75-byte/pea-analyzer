from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
import json
import traceback

import pandas as pd

from v182.reporting import action_ct_shadow_bundle_run as action_ct_bundle
from v182.reporting import tct_daily_trader_shadow_run_v24_3_1 as tct_trader


ROOT = Path(__file__).resolve().parents[3]
VERSION = "V21.13.11_TACTICAL_SHARED_PARQUET_RUNTIME"


@dataclass
class ParquetReadCache:
    """Cache only plain path-only pandas.read_parquet calls for one process.

    The governed extractors remain untouched. Each logical caller receives a
    deep copy of the raw DataFrame, so model-specific transformations cannot
    leak across Action CT V22.0/V22.1 and TCT V24.3.1.

    Calls with positional/keyword options are deliberately passed through to
    pandas unchanged instead of trying to infer an equivalent cache key.
    """

    original_reader: Callable[..., pd.DataFrame]
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    logical_calls: int = 0
    cache_hits: int = 0
    physical_reads: int = 0
    physical_read_seconds: float = 0.0
    passthrough_calls: int = 0

    def __call__(self, path: Any, *args: Any, **kwargs: Any) -> pd.DataFrame:
        self.logical_calls += 1
        if args or kwargs or not isinstance(path, (str, Path)):
            self.passthrough_calls += 1
            return self.original_reader(path, *args, **kwargs)

        key = str(Path(path).resolve())
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
        return {
            "logical_read_parquet_calls": int(self.logical_calls),
            "physical_read_parquet_calls": int(self.physical_reads),
            "cache_hits": int(self.cache_hits),
            "passthrough_calls": int(self.passthrough_calls),
            "unique_cached_paths": int(len(self.frames)),
            "physical_read_seconds": round(float(self.physical_read_seconds), 6),
            "raw_consumer_isolation": "DEEP_COPY_PER_READ",
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


def run(root: Path = ROOT) -> dict:
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    original_read_parquet = pd.read_parquet
    parquet_cache = ParquetReadCache(original_read_parquet)
    setattr(pd, "read_parquet", parquet_cache)
    try:
        action_ct, action_ct_error = _run_step(
            "ACTION_CT_V22.0_V22.1",
            lambda: action_ct_bundle.run(root=root),
        )
        tct, tct_error = _run_step(
            "TCT_V24.3.1",
            lambda: tct_trader.run(root=root),
        )
    finally:
        setattr(pd, "read_parquet", original_read_parquet)

    errors = [error for error in (action_ct_error, tct_error) if error is not None]
    payload = {
        "status": "SUCCESS_TACTICAL_SHARED_RUNTIME" if not errors else "TACTICAL_SHARED_RUNTIME_WITH_STEP_ERRORS",
        "version": VERSION,
        "model_order_preserved": ["ACTION_CT_V22.0", "ACTION_CT_V22.1", "TCT_V24.3.1"],
        "original_pandas_reader_restored": pd.read_parquet is original_read_parquet,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "governed_extractors_changed": False,
        "t1_t2_scope_changed": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
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
            "Tactical shared-parquet bundle completed with step error(s): "
            + ", ".join(str(error.get("step")) for error in errors)
        )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
