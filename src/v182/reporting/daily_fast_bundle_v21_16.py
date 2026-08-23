from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import time
import traceback

from v182.reporting import daily_fast_collection
from v182.reporting import daily_tct_ct_runner
from v182.reporting import tct_postmarket_bundle_run
from v182.reporting.runtime_telemetry import _budget_result

ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_FAST_BUNDLE_V21_16_2"


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


def run(root: Path = ROOT) -> dict:
    started = time.perf_counter()
    steps: dict[str, dict] = {}
    steps["fast_collection"] = _timed(
        "FAST_COLLECTION", lambda: daily_fast_collection.run(root), blocking=True
    )
    steps["tct_ct"] = _timed(
        "TCT_CT", lambda: daily_tct_ct_runner.run(root), blocking=True
    )
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
        "step_order": ["fast_collection", "tct_ct", "postmarket"],
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
