from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from typing import Callable
import json
import os
import traceback

from v182.reporting import decision_brief
from v182.reporting import etf_structure_state_replay as etf_replay
from v182.reporting import friday_tactical_reuse_runner as friday_reuse
from v182.reporting import tactical_shadow_bundle_run as tactical
from v182.reporting import tct_postmarket_bundle_run as postmarket
from v182.reporting import weekly_post_decision_bundle_run as weekly_post


ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_TAIL_SUPER_RUNTIME_V21_16_0"
AUDIT_NAME = "WEEKLY_TAIL_SUPER_RUNTIME_V21_16_0.json"


def _capture(name: str, runner: Callable[[], dict]) -> dict:
    started = perf_counter()
    try:
        payload = runner()
        return {
            "step": name,
            "status": "SUCCESS",
            "runtime_seconds": round(float(perf_counter() - started), 6),
            "result": payload if isinstance(payload, dict) else {},
            "error": None,
        }
    except Exception as exc:
        return {
            "step": name,
            "status": "FAILED",
            "runtime_seconds": round(float(perf_counter() - started), 6),
            "result": {},
            "error": {
                "type": type(exc).__name__,
                "message": str(exc)[:700],
                "traceback": traceback.format_exc(limit=7),
            },
        }


def _brief_and_post_decision_parallel(root: Path) -> tuple[dict, dict]:
    """Overlap local CI brief generation with the independent weekly post bundle."""
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="weekly-finalize") as pool:
        brief_future = pool.submit(
            _capture,
            "DECISION_BRIEF",
            lambda: decision_brief.run(root=root),
        )
        post_future = pool.submit(
            _capture,
            "WEEKLY_POST_DECISION_BUNDLE",
            lambda: weekly_post.run(root=root),
        )
        return brief_future.result(), post_future.result()


def _deferred(step: str) -> dict:
    return {
        "step": step,
        "status": "DEFERRED_DISTINCT_SHADOW_PROCESS",
        "runtime_seconds": 0.0,
        "result": {},
        "error": None,
    }


def run(root: Path = ROOT) -> dict:
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    steps: dict[str, dict] = {}
    critical_only = os.environ.get("PEA_WEEKLY_CRITICAL_ONLY", "0").strip() == "1"

    if critical_only:
        steps["etf_structure_replay"] = _deferred("ETF_STRUCTURE_STATE_REPLAY")
    else:
        steps["etf_structure_replay"] = _capture(
            "ETF_STRUCTURE_STATE_REPLAY",
            lambda: etf_replay.run(root=root),
        )

    steps["friday_tactical_reuse"] = _capture(
        "FRIDAY_TACTICAL_REUSE",
        lambda: friday_reuse.run(root=root),
    )
    if steps["friday_tactical_reuse"]["status"] != "SUCCESS":
        payload = {
            "status": "FAILED_REQUIRED_FRIDAY_TACTICAL_REUSE",
            "version": VERSION,
            "total_seconds": round(float(perf_counter() - started), 6),
            "steps": steps,
            "decision_logic_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "pit_logic_changed": False,
            "holdout_opened": False,
            "real_orders_enabled": False,
        }
        (auditdir / AUDIT_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        raise RuntimeError(
            "WEEKLY_TAIL_REQUIRED_FRIDAY_REUSE_FAILED:"
            + str(steps["friday_tactical_reuse"].get("error"))
        )

    postmarket_holder: dict[str, dict] = {}

    def _start_postmarket(_tct_payload: dict, _tct_error: dict | None) -> None:
        postmarket_holder["step"] = _capture(
            "POSTMARKET_V24.4.2",
            lambda: postmarket.run(root=root),
        )

    if critical_only:
        steps["tactical_shadow"] = _deferred("TACTICAL_SHADOW_BUNDLE")
    else:
        steps["tactical_shadow"] = _capture(
            "TACTICAL_SHADOW_BUNDLE",
            lambda: tactical.run(root=root, tct_complete_callback=_start_postmarket),
        )
    if not critical_only and "step" not in postmarket_holder:
        postmarket_holder["step"] = _capture(
            "POSTMARKET_V24.4.2",
            lambda: postmarket.run(root=root),
        )
    steps["postmarket"] = postmarket_holder.get("step", _deferred("POSTMARKET_V24.4.2"))

    brief, weekly_post_result = _brief_and_post_decision_parallel(root)
    steps["decision_brief"] = brief
    steps["weekly_post_decision"] = weekly_post_result

    required_failures = [
        name
        for name in ("decision_brief", "weekly_post_decision")
        if steps[name]["status"] != "SUCCESS"
    ]
    advisory_failures = [
        name
        for name in ("etf_structure_replay", "tactical_shadow", "postmarket")
        if steps[name]["status"] not in {"SUCCESS", "DEFERRED_DISTINCT_SHADOW_PROCESS"}
    ]
    status = (
        "SUCCESS_WEEKLY_TAIL_OPTIMIZED"
        if not required_failures and not advisory_failures
        else "SUCCESS_WEEKLY_TAIL_WITH_ADVISORY_WARNINGS"
        if not required_failures
        else "FAILED_REQUIRED_WEEKLY_FINALIZATION"
    )

    payload = {
        "status": status,
        "version": VERSION,
        "total_seconds": round(float(perf_counter() - started), 6),
        "required_failures": required_failures,
        "advisory_failures": advisory_failures,
        "steps": steps,
        "optimization_contract": {
            "friday_committee_score_recompute_removed": True,
            "friday_tct_baseline_recompute_removed": True,
            "friday_tct_exact_recompute_removed": True,
            "friday_v21_8_second_application_removed": True,
            "postmarket_overlapped_after_tct_completion": True,
            "decision_brief_overlaps_weekly_post_decision": True,
            "provider_freshness_policy_changed": False,
            "external_provider_concurrency_added_by_finalization": False,
            "python_processes_consolidated": True,
            "critical_path_only": critical_only,
            "shadow_diagnostics_deferred_to_distinct_process": critical_only,
            "etf_structure_replay_deferred_on_critical_path": critical_only,
            "deferred_modules_decision_influence": 0.0,
        },
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "candidate_scope_changed": False,
        "pit_logic_changed": False,
        "fingerprint_logic_changed": False,
        "holdout_opened": False,
        "t1_t2_scope_changed": False,
        "real_orders_enabled": False,
    }
    (auditdir / AUDIT_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    if required_failures:
        raise RuntimeError(
            "WEEKLY_TAIL_REQUIRED_FINALIZATION_FAILED:" + ",".join(required_failures)
        )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
