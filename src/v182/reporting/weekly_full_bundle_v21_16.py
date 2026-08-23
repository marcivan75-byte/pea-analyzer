from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import time
import traceback

import pandas as pd

from v182.reporting import friday_tactical_reuse_v21_16
from v182.reporting import weekly_tail_parallel_v21_16
from v182.reporting import weekly_unified_fast_v21_16
from v182.reporting.daily_source_prewarm_v21_16 import WEEKLY_SEED_PATH, persist_seed
from v182.reporting.runtime_telemetry import _budget_result

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_FULL_BUNDLE_V21_16_2_PREWARM_SEED"


def _step(name: str, func) -> dict:
    started = time.perf_counter()
    try:
        result = func()
        return {
            "status": "SUCCESS",
            "wall_seconds": round(time.perf_counter() - started, 6),
            "result": result,
        }
    except Exception as exc:
        return {
            "status": "FAILED",
            "wall_seconds": round(time.perf_counter() - started, 6),
            "error": type(exc).__name__,
            "detail": str(exc)[:500],
            "traceback": traceback.format_exc(limit=8),
        }


def _persist_weekly_prewarm_seed(root: Path) -> dict:
    path = root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv"
    if not path.exists():
        return {"status": "COMMITTEE_DECISIONS_MISSING", "persisted_rows": 0}
    try:
        frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
        return persist_seed(
            frame,
            root,
            seed_path=WEEKLY_SEED_PATH,
            max_persisted=40,
        )
    except Exception as exc:
        return {
            "status": "FAILED_NONBLOCKING",
            "persisted_rows": 0,
            "error": type(exc).__name__,
            "detail": str(exc)[:400],
        }


def run(root: Path = ROOT) -> dict:
    started = time.perf_counter()
    steps: dict[str, dict] = {}

    steps["unified"] = _step("UNIFIED", lambda: weekly_unified_fast_v21_16.run(root))
    unified_result = steps["unified"].get("result") if isinstance(steps["unified"].get("result"), dict) else {}
    if steps["unified"]["status"] != "SUCCESS" or unified_result.get("status") != "SUCCESS":
        error = steps["unified"].get("error") or f"UNIFIED_STATUS_{unified_result.get('status', 'UNKNOWN')}"
        raise RuntimeError(f"WEEKLY_FULL_BUNDLE_UNIFIED_FAILED:{error}")

    weekly_prewarm_seed = _persist_weekly_prewarm_seed(root)

    steps["friday_tactical_reuse"] = _step(
        "FRIDAY_TACTICAL_REUSE", lambda: friday_tactical_reuse_v21_16.run(root)
    )
    if steps["friday_tactical_reuse"]["status"] != "SUCCESS":
        raise RuntimeError(
            "WEEKLY_FULL_BUNDLE_FRIDAY_REUSE_FAILED:"
            + str(steps["friday_tactical_reuse"].get("error") or "UNKNOWN")
        )

    steps["weekly_tail"] = _step(
        "WEEKLY_TAIL", lambda: weekly_tail_parallel_v21_16.run(root)
    )
    if steps["weekly_tail"]["status"] != "SUCCESS":
        raise RuntimeError(
            "WEEKLY_FULL_BUNDLE_TAIL_FAILED:"
            + str(steps["weekly_tail"].get("error") or "UNKNOWN")
        )

    wall_seconds = round(time.perf_counter() - started, 6)
    payload = {
        "status": "SUCCESS",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "wall_seconds": wall_seconds,
        "duration_contract": _budget_result("WEEKLY_FULL_COMMITTEE", wall_seconds),
        "previous_parent_python_processes": 3,
        "current_parent_python_processes": 1,
        "parent_interpreter_startups_avoided": 2,
        "next_week_source_prewarm_seed": weekly_prewarm_seed,
        "weekly_source_prewarm_max_unique_isins": 40,
        "step_order": ["unified", "friday_tactical_reuse", "weekly_tail"],
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "universe_changed": False,
        "real_orders_enabled": False,
        "steps": steps,
    }
    audit = root / "outputs" / "audit" / "WEEKLY_FULL_BUNDLE_V21_16.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
