from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import json
import time
import traceback

import pandas as pd

from v182.reporting import daily_fast_collection
from v182.reporting import daily_tct_ct_runner
from v182.reporting import tct_postmarket_bundle_run
from v182.reporting.daily_source_prewarm_v21_16 import persist_seed, prewarm
from v182.reporting.runtime_telemetry import _budget_result

ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_FAST_BUNDLE_V21_16_4_PREWARM"


def _timed(name: str, func, *, blocking: bool) -> dict:
    started = time.perf_counter()
    try:
        result = func()
        return {
            "status": "SUCCESS",
            "blocking": blocking,
            "wall_seconds": round(time.perf_counter() - started, 6),
            "result": result,
        }
    except Exception as exc:
        payload = {
            "status": "FAILED",
            "blocking": blocking,
            "wall_seconds": round(time.perf_counter() - started, 6),
            "error": type(exc).__name__,
            "detail": str(exc)[:500],
            "traceback": traceback.format_exc(limit=6),
        }
        if blocking:
            raise RuntimeError(f"DAILY_FAST_BUNDLE_{name}_FAILED:{type(exc).__name__}:{str(exc)[:240]}") from exc
        return payload


def _collect_in_memory(root: Path) -> tuple[dict, object, object]:
    started = time.perf_counter()
    try:
        result, actions, etfs = daily_fast_collection.run(
            root,
            persist_masters=False,
            persist_daily_baseline=False,
            persist_auxiliary_outputs=False,
            return_frames=True,
        )
    except Exception as exc:
        raise RuntimeError(f"DAILY_FAST_BUNDLE_FAST_COLLECTION_FAILED:{type(exc).__name__}:{str(exc)[:240]}") from exc
    step = {
        "status": "SUCCESS",
        "blocking": True,
        "wall_seconds": round(time.perf_counter() - started, 6),
        "result": result,
    }
    return step, actions, etfs


def _prewarm_timed(root: Path) -> dict:
    started = time.perf_counter()
    try:
        result = prewarm(root)
        return {
            "status": "SUCCESS",
            "blocking": False,
            "wall_seconds": round(time.perf_counter() - started, 6),
            "result": result,
        }
    except Exception as exc:
        return {
            "status": "FAILED_NONBLOCKING",
            "blocking": False,
            "wall_seconds": round(time.perf_counter() - started, 6),
            "error": type(exc).__name__,
            "detail": str(exc)[:500],
        }


def _persist_next_seed(root: Path) -> dict:
    governed = root / "outputs" / "daily_tct_ct" / "DAILY_TCT_CT_V21_8.csv"
    if not governed.exists():
        return {"status": "DECISION_OUTPUT_MISSING", "persisted_rows": 0}
    try:
        frame = pd.read_csv(governed, sep=";", encoding="utf-8-sig", low_memory=False)
        return persist_seed(frame, root)
    except Exception as exc:
        return {
            "status": "FAILED_NONBLOCKING",
            "persisted_rows": 0,
            "error": type(exc).__name__,
            "detail": str(exc)[:300],
        }


def run(root: Path = ROOT) -> dict:
    started = time.perf_counter()
    steps: dict[str, dict] = {}

    # Previous-selection Boursorama/Investing uses independent providers from the
    # full-universe Yahoo OHLCV refresh. Hide speculative cache warming under the
    # unavoidable market refresh, then wait before current decisions use caches.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="daily-source-prewarm") as pool:
        prewarm_future = pool.submit(_prewarm_timed, root)
        collection_step, actions, etfs = _collect_in_memory(root)
        steps["fast_collection"] = collection_step
        steps["source_prewarm"] = prewarm_future.result()

    steps["tct_ct"] = _timed(
        "TCT_CT",
        lambda: daily_tct_ct_runner.run(
            root,
            actions=actions,
            etfs=etfs,
            persist_full_baseline=False,
        ),
        blocking=True,
    )
    del actions, etfs
    next_seed = _persist_next_seed(root)
    steps["postmarket"] = _timed(
        "POSTMARKET", lambda: tct_postmarket_bundle_run.run(root), blocking=False
    )

    wall_seconds = round(time.perf_counter() - started, 6)
    payload = {
        "status": "SUCCESS" if steps["postmarket"]["status"] == "SUCCESS" else "SUCCESS_WITH_NONBLOCKING_POSTMARKET_ERROR",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "wall_seconds": wall_seconds,
        "duration_contract": _budget_result("DAILY_TACTICAL", wall_seconds),
        "previous_python_processes": 3,
        "current_python_processes": 1,
        "interpreter_startups_avoided": 2,
        "full_master_csv_roundtrip_avoided": True,
        "weekly_baseline_parquet_rewrite_avoided": True,
        "daily_auxiliary_sector_files_skipped": True,
        "compact_tct_baseline_export": True,
        "prior_source_prewarm_parallel_with_ohlcv": True,
        "prior_source_prewarm_max_unique_isins": 20,
        "prewarm_failure_nonblocking": True,
        "current_source_gate_coverage_reduced": False,
        "next_daily_source_prewarm_seed": next_seed,
        "step_order": ["fast_collection||source_prewarm", "tct_ct", "postmarket"],
        "postmarket_failure_is_nonblocking": True,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "universe_changed": False,
        "real_orders_enabled": False,
        "steps": steps,
    }
    audit = root / "outputs" / "audit" / "DAILY_FAST_BUNDLE_V21_16.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
