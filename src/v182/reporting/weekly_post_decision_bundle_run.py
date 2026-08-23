from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import time

from v182.reporting import criteria_governance_audit
from v182.reporting import decision_brief
from v182.reporting import etf_fund_flows_shadow_run


ROOT = Path(__file__).resolve().parents[3]
AUDIT_NAME = "WEEKLY_POST_DECISION_BUNDLE_RUNTIME_V21_15_2.json"


def _run_measured(name: str, func, root: Path) -> dict:
    started = time.perf_counter()
    try:
        result = func(root)
        return {
            "step": name,
            "status": "SUCCESS",
            "runtime_seconds": round(time.perf_counter() - started, 3),
            "result_status": result.get("status") if isinstance(result, dict) else None,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - preserve workflow failure semantics in audit
        return {
            "step": name,
            "status": "FAILED",
            "runtime_seconds": round(time.perf_counter() - started, 3),
            "result_status": None,
            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
        }


def _write_audit(root: Path, payload: dict) -> None:
    outdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / AUDIT_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run(root: Path = ROOT) -> dict:
    """Bundle the weekly post-tactical tail without changing business semantics.

    Historical workflow semantics are preserved:
    1. Decision Brief is mandatory and must finish before later work starts.
    2. ETF Fund Flows is SHADOW / continue-on-error.
    3. Criteria governance is mandatory.

    Only Fund Flows and the local governance audit overlap. Fund Flows remains
    the sole external-collection branch in that overlap, so provider concurrency
    is not increased.
    """
    started = time.perf_counter()
    steps: dict[str, dict] = {}

    decision = _run_measured("DECISION_BRIEF", decision_brief.run, root)
    steps["decision_brief"] = decision
    if decision["status"] != "SUCCESS":
        payload = {
            "status": "FAILED_DECISION_BRIEF",
            "runtime_seconds": round(time.perf_counter() - started, 3),
            "steps": steps,
            "decision_brief_required": True,
            "fund_flows_shadow_optional": True,
            "criteria_governance_required": True,
            "later_steps_started": False,
            "previous_python_processes": 3,
            "current_python_processes": 1,
            "interpreter_startups_avoided": 2,
            "governance_overlaps_fund_flows": False,
            "external_provider_concurrency_added": False,
            "decision_logic_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "pit_logic_changed": False,
            "holdout_opened": False,
            "real_orders_enabled": False,
        }
        _write_audit(root, payload)
        raise RuntimeError(f"WEEKLY_POST_DECISION_BUNDLE:DECISION_BRIEF:{decision['error']}")

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="weekly-post-decision") as pool:
        fund_future = pool.submit(_run_measured, "ETF_FUND_FLOWS_SHADOW", etf_fund_flows_shadow_run.run, root)
        governance_future = pool.submit(_run_measured, "CRITERIA_GOVERNANCE", criteria_governance_audit.run, root)
        fund = fund_future.result()
        governance = governance_future.result()

    steps["etf_fund_flows_shadow"] = fund
    steps["criteria_governance"] = governance
    if governance["status"] != "SUCCESS":
        status = "FAILED_CRITERIA_GOVERNANCE"
    elif fund["status"] != "SUCCESS":
        status = "SUCCESS_WITH_FUND_FLOW_WARNING"
    else:
        status = "SUCCESS_WEEKLY_POST_DECISION_SINGLE_PROCESS"

    payload = {
        "status": status,
        "runtime_seconds": round(time.perf_counter() - started, 3),
        "steps": steps,
        "decision_brief_required": True,
        "fund_flows_shadow_optional": True,
        "criteria_governance_required": True,
        "later_steps_started": True,
        "previous_python_processes": 3,
        "current_python_processes": 1,
        "interpreter_startups_avoided": 2,
        "governance_overlaps_fund_flows": True,
        "external_provider_concurrency_added": False,
        "fund_flow_failure_blocks_workflow": False,
        "governance_failure_blocks_workflow": True,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "pit_logic_changed": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
    }
    _write_audit(root, payload)

    if governance["status"] != "SUCCESS":
        raise RuntimeError(f"WEEKLY_POST_DECISION_BUNDLE:CRITERIA_GOVERNANCE:{governance['error']}")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
