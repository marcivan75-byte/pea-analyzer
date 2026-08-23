from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Callable
import json
import os
import traceback

from v182.reporting import tct_next_session_catalyst_run_v24_4_2 as catalyst
from v182.reporting import tct_pit_ohlc_ledger_v24_4_2 as ohlc_ledger
from v182.reporting import tct_v24_4_2_pit_lineage as lineage
from v182.reporting import tct_v24_4_2_pit_validator as validator


ROOT = Path(__file__).resolve().parents[3]
VERSION = "V21.16.1_TCT_POSTMARKET_PROFILED_RUNTIME"


def _run_step(name: str, runner: Callable[[], dict]) -> tuple[dict, dict | None, float]:
    started = perf_counter()
    try:
        payload = runner()
        return payload, None, perf_counter() - started
    except Exception as exc:
        return {}, {
            "step": name,
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": traceback.format_exc(limit=5),
        }, perf_counter() - started


def run(root: Path = ROOT) -> dict:
    """Run the operational POSTMARKET chain with PIT audit work deferred to Friday.

    Mon-Thu DAILY_TACTICAL keeps the two inputs needed for next-session context:
    the governed OHLC ledger and the selected-candidate catalyst/news snapshot.
    PIT lineage and the historical validator remain owned by the Friday full run;
    they have zero score/decision influence and therefore need not consume daily
    GitHub minutes.
    """
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    daily_fast = os.environ.get("PEA_RUN_PROFILE", "").strip().upper() == "DAILY_TACTICAL"

    specifications: list[tuple[str, Callable[[], dict]]] = [
        ("PIT_OHLC_V24.4.2", lambda: ohlc_ledger.run(root=root)),
        ("POSTMARKET_CATALYST_V24.4.2", lambda: catalyst.run(root=root, phase="POSTMARKET")),
    ]
    deferred: list[str] = []
    if daily_fast:
        deferred = ["PIT_LINEAGE_V24.4.2", "PIT_VALIDATOR_V24.4.2"]
    else:
        specifications.extend(
            [
                ("PIT_LINEAGE_V24.4.2", lambda: lineage.run(root=root)),
                ("PIT_VALIDATOR_V24.4.2", lambda: validator.run(root=root)),
            ]
        )

    step_payloads: dict[str, dict] = {}
    step_runtime: dict[str, float] = {}
    errors: list[dict] = []
    for name, runner in specifications:
        payload, error, seconds = _run_step(name, runner)
        step_payloads[name] = payload
        step_runtime[name] = round(float(seconds), 6)
        if error is not None:
            errors.append(error)

    result = {
        "status": "SUCCESS_POSTMARKET_PROFILED" if not errors else "POSTMARKET_PROFILED_WITH_STEP_ERRORS",
        "version": VERSION,
        "profile": "DAILY_TACTICAL_FAST" if daily_fast else "WEEKLY_FULL_FRIDAY",
        "step_order": [name for name, _ in specifications],
        "deferred_to_weekly_friday": deferred,
        "operational_steps_attempted": True,
        "postmarket_phase_explicit": True,
        "python_processes": 1,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "candidate_scope_changed": False,
        "news_query_policy_changed": False,
        "pit_score_logic_changed": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
        "daily_deferred_steps_have_decision_influence": False,
        "step_runtime_seconds": step_runtime,
        "steps": {
            name: {
                "status": payload.get("status"),
                "version": payload.get("version"),
            }
            for name, payload in step_payloads.items()
        },
        "errors": errors,
        "total_seconds": round(float(perf_counter() - started), 6),
    }
    audit_path = auditdir / "POSTMARKET_BUNDLE_RUNTIME_V21_13_12.json"
    audit_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if errors:
        raise RuntimeError(
            "POSTMARKET V21.16.1 completed with step error(s): "
            + ", ".join(str(error.get("step")) for error in errors)
        )
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
