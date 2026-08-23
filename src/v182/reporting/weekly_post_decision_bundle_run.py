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


def _measured(name: str, func, root: Path) -> dict:
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
    except Exception as exc:  # preserve existing workflow failure semantics in the bundle audit
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
    """Preserve the weekly tail semantics while hiding local audit time under Fund Flows I/O.

    Historical workflow contract:
    - Decision Brief is mandatory. If it fails, the later two steps never start.
    - ETF Fund Flows is SHADOW and continue-on-error.
    - Criteria Governance is mandatory.

    Once the Decision Brief succeeds, Fund Flows and Criteria Governance are independent:
    the former writes fund-flow state/outputs, the latter reads masters/config and writes audit
    outputs. They therefore execute concurrently without adding a second external-provider branch.
    """
    started = time.perf_counter()
    steps: dict[str, dict] = {}

    brief = _measured("DECISION_BRIEF", decision_brief.run, root)
    steps["decision_brief"] = brief
    if brief["status"] != "SUCCESS":
        payload = {
            "status": "FAILED_DECISION_BRIEF",
            "runtime_seconds": round(time.perf_counter() - started, 3),
            "steps": steps,
            "later_steps_started": False,
            "decision_brief_required": True,
            "fund_flows_shadow_continue_on_error": True,
            "criteria_governance_required": True,
            "governance_overlaps_fund_flows": False,
            "external_provider_concurrency_added": False,
            "previous_python_processes": 3,
            "current_python_processes": 1,
            "interpreter_startups_avoided": 2,
            "decision_logic_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "pit_logic_changed": False,
            "holdout_opened": False,
            "real_orders_enabled": False,
        }
        _write_audit(root, payload)
        raise RuntimeError(f"WEEKLY_POST_DECISION_BUNDLE:DECISION_BRIEF:{brief['error']}")

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="weekly-post-decision") as pool:
        fund_future = pool.submit(_measured, "ETF_FUND_FLOWS_SHADOW", etf_fund_flows_shadow_run.run, root)
        governance_future = pool.submit(_measured, "CRITERIA_GOVERNANCE", criteria_governance_audit.run, root)
        fund = fund_future.result()
        governance = governance_future.result()

    steps["etf_fund_flows_shadow"] = fund
    steps["criteria_governance"] = governance

    if governance["status"] != "SUCCESS":
        status = "FAILED_CRITERIA_GOVERNANCE"
    elif fund["status"] != "SUCCESS":
        status = "SUCCESS_WITH_FUND_FLOW_WARNING"
    else:
        status = "SUCCESS_WEEKLY_POST_DECISION_BUNDLE"

    payload = {
        "status": status,
        "runtime_seconds": round(time.perf_counter() - started, 3),
        "steps": steps,
        "later_steps_started": True,
        "decision_brief_required": True,
        "fund_flows_shadow_continue_on_error": True,
        "criteria_governance_required": True,
        "governance_overlaps_fund_flows": True,
        "external_provider_concurrency_added": False,
        "previous_python_processes": 3,
        "current_python_processes": 1,
        "interpreter_startups_avoided": 2,
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
