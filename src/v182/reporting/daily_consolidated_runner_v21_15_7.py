from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
import traceback

from v182.reporting import daily_ci_restitution_v21_15_7 as daily_ci
from v182.reporting import daily_consolidated_runner_v21_15_5 as base
from v182.reporting import daily_tactical_super_runner_v21_15_6 as tactical
from v182.reporting import daily_w09_seed_v21_15_7 as w09_seed


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_CONSOLIDATED_RUNTIME_V21_15_7"


def _write_final_audit(root: Path, payload: dict) -> None:
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    for name in (
        "DAILY_CONSOLIDATED_RUNTIME_V21_15_7.json",
        "DAILY_CONSOLIDATED_RUNTIME_V21_15_6.json",
        "DAILY_CONSOLIDATED_RUNTIME_V21_15_5.json",
        "DAILY_CONSOLIDATED_RUNTIME_V21_15_4.json",
    ):
        (auditdir / name).write_text(text, encoding="utf-8")


def _write_ci_failure_audit(root: Path, exc: Exception, elapsed_seconds: float) -> None:
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "FAILED_CI_RESTITUTION",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "ci_elapsed_seconds": round(float(elapsed_seconds), 6),
        "traceback": traceback.format_exc(),
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "real_orders_enabled": False,
    }
    (auditdir / "DAILY_CI_FAILURE_V21_15_7.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def run(root: Path = ROOT) -> dict:
    """Final Daily: zero-network W09, bounded tactical engines and same-run CI restitution."""
    started = perf_counter()
    original_tactical = base.tactical
    original_version = base.VERSION
    base.tactical = tactical
    base.VERSION = VERSION
    try:
        payload = dict(base.run(root=root) or {})
    finally:
        base.tactical = original_tactical
        base.VERSION = original_version

    base_status = str(payload.get("status") or "")
    ci_started = perf_counter()
    try:
        ci_payload = daily_ci.run(root=root)
    except Exception as exc:
        _write_ci_failure_audit(root, exc, perf_counter() - ci_started)
        raise
    ci_seconds = perf_counter() - ci_started

    timings = dict(payload.get("timings_seconds") or {})
    timings["ci_restitution"] = round(float(ci_seconds), 6)
    timings["total"] = round(float(perf_counter() - started), 6)
    steps = dict(payload.get("steps") or {})
    steps["ci_restitution"] = {
        "status": ci_payload.get("status"),
        "version": ci_payload.get("version"),
        "selected_rows": ci_payload.get("selected_rows"),
        "word_output": ci_payload.get("word_output"),
        "excel_output": ci_payload.get("excel_output"),
    }
    final_status = (
        "SUCCESS_DAILY_CONSOLIDATED_WITH_CI_AND_ETF_REPLAY_WARNING"
        if "ETF_REPLAY_WARNING" in base_status
        else "SUCCESS_DAILY_CONSOLIDATED_WITH_CI"
    )

    payload.update({
        "status": final_status,
        "base_status_before_ci": base_status,
        "version": VERSION,
        "tactical_runtime_version": tactical.VERSION,
        "daily_ci_version": daily_ci.VERSION,
        "wave09_refresh_cadence": "WEEKLY_ONLY",
        "wave09_daily_network_calls": 0,
        "wave09_bootstrap_seed": w09_seed.audit_contract(),
        "weekly_snapshot_preferred_when_available": True,
        "legacy_validated_w09_seed_used_only_when_fast_or_weekly_master_missing": True,
        "committee_model_reruns": 0,
        "committee_external_collection_calls": 0,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "t1_t2_scope_changed": False,
        "real_orders_enabled": False,
        "steps": steps,
        "timings_seconds": timings,
    })
    _write_final_audit(root, payload)
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
