from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import time

from v182.reporting import criteria_governance_audit
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
    """Overlap only the two weekly steps that historically run after Decision Brief.

    The Decision Brief intentionally remains a separate GitHub step with `if: always()`.
    This bundle has the default GitHub success gate, exactly like the historical Fund
    Flows and Criteria Governance steps: if an earlier mandatory step (including the
    Decision Brief) failed, GitHub does not invoke this module.

    Inside the module, ETF Fund Flows remains SHADOW / continue-on-error while Criteria
    Governance remains mandatory. The two branches have disjoint writes and only Fund
    Flows performs external collection, so the overlap adds no provider concurrency.
    """
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="weekly-post-decision") as pool:
        fund_future = pool.submit(_measured, "ETF_FUND_FLOWS_SHADOW", etf_fund_flows_shadow_run.run, root)
        governance_future = pool.submit(_measured, "CRITERIA_GOVERNANCE", criteria_governance_audit.run, root)
        fund = fund_future.result()
        governance = governance_future.result()

    steps = {
        "etf_fund_flows_shadow": fund,
        "criteria_governance": governance,
    }
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
        "decision_brief_remains_separate_required_gate": True,
        "fund_flows_shadow_continue_on_error": True,
        "criteria_governance_required": True,
        "governance_overlaps_fund_flows": True,
        "external_provider_concurrency_added": False,
        "previous_python_processes_after_brief": 2,
        "current_python_processes_after_brief": 1,
        "interpreter_startups_avoided": 1,
        "workflow_failure_semantics_changed": False,
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
