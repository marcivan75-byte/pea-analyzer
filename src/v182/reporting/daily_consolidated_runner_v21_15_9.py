from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json

from v182.reporting import daily_consolidated_runner_v21_15_7 as impl
from v182.reporting import daily_market_orientation_v21_15_9 as market_orientation


ROOT = impl.ROOT
VERSION = "DAILY_CONSOLIDATED_RUNTIME_V21_15_9"


def _patch_json(path: Path, patch: dict) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return
    payload.update(patch)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def run(root: Path = ROOT) -> dict:
    started = perf_counter()
    orientation_started = perf_counter()
    try:
        orientation = market_orientation.run(root=root)
    except Exception as exc:
        orientation = {
            "status": "FAILED_NON_BLOCKING",
            "version": market_orientation.VERSION,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "orientation": "UNAVAILABLE",
            "indicators": [],
            "decision_influence": False,
            "score_influence": 0.0,
            "weights_changed": False,
            "thresholds_changed": False,
            "criteria_changed": False,
            "real_orders_enabled": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }
    orientation_seconds = perf_counter() - orientation_started

    payload = dict(impl.run(root=root) or {})

    publish_started = perf_counter()
    try:
        ci_publish = market_orientation.publish_ci_context(root, orientation)
        ci_publish_status = "SUCCESS"
    except Exception as exc:
        ci_publish = {"error_type": type(exc).__name__, "error": str(exc)[:500]}
        ci_publish_status = "FAILED_NON_BLOCKING"
    publish_seconds = perf_counter() - publish_started

    market_step = {
        "status": orientation.get("status"),
        "version": market_orientation.VERSION,
        "orientation": orientation.get("orientation"),
        "live_indicators": orientation.get("live_indicators"),
        "usable_indicators": orientation.get("usable_indicators"),
        "indicators": orientation.get("indicators"),
        "decision_influence": False,
        "score_influence": 0.0,
        "can_create_buy": False,
        "can_block_buy": False,
        "ci_publish_status": ci_publish_status,
        "ci_outputs": ci_publish,
    }
    steps = dict(payload.get("steps") or {})
    steps["market_orientation_upstream"] = market_step
    timings = dict(payload.get("timings_seconds") or {})
    timings["market_orientation_upstream"] = round(float(orientation_seconds), 6)
    timings["market_orientation_ci_publish"] = round(float(publish_seconds), 6)
    timings["total_with_market_orientation"] = round(float(perf_counter() - started), 6)

    payload.update({
        "version": VERSION,
        "market_orientation_version": market_orientation.VERSION,
        "market_orientation_scope": "DAILY_UPSTREAM_LIGHT_CONTEXT_ONLY",
        "market_orientation": market_step,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "t1_t2_scope_changed": False,
        "real_orders_enabled": False,
        "steps": steps,
        "timings_seconds": timings,
    })

    audit_patch = {
        "market_orientation_version": market_orientation.VERSION,
        "market_orientation": market_step,
        "market_orientation_decision_influence": False,
        "market_orientation_score_influence": 0.0,
    }
    for name in (
        "DAILY_CI_RESTITUTION_V21_15_7.json",
        "CI_EXPLAINABILITY_AUDIT.json",
    ):
        _patch_json(root / "outputs" / "audit" / name, audit_patch)

    impl._write_final_audit(root, payload)
    return payload


# Compatibility aliases for downstream callers/tests.
base = impl.base
collection = impl.base.collection
etf_replay = impl.base.etf_replay
wave3_cpu = impl.base.wave3_cpu
refresh_earnings_clock = impl.base.refresh_earnings_clock
tactical = impl.tactical


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
