from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json

from v182.reporting import daily_consolidated_runner_v21_15_9 as impl
from v182.reporting import selected_source_reliability_v21_8_4 as source_reliability
from v182.reporting import daily_ci_light_v21_8_5 as ci_light


ROOT = impl.ROOT
VERSION = "DAILY_CONSOLIDATED_RUNTIME_V21_15_11"


def _patch_json(path: Path, patch: dict) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    payload.update(patch)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def run(root: Path = ROOT) -> dict:
    started = perf_counter()
    source_patch = source_reliability.install()
    payload = dict(impl.run(root=root) or {})

    ci_started = perf_counter()
    try:
        ci_payload = ci_light.run(root)
        ci_status = "SUCCESS"
    except Exception as exc:
        ci_payload = {"error_type": type(exc).__name__, "error": str(exc)[:500]}
        ci_status = "FAILED_NON_BLOCKING"
    ci_seconds = perf_counter() - ci_started

    steps = dict(payload.get("steps") or {})
    steps["source_reliability_v21_8_5"] = source_patch
    steps["ci_light_v21_8_5"] = {
        "status": ci_status,
        **ci_payload,
        "decision_influence": False,
        "score_influence": 0.0,
        "real_orders_enabled": False,
    }
    timings = dict(payload.get("timings_seconds") or {})
    timings["ci_light_v21_8_5"] = round(ci_seconds, 6)
    timings["total_v21_15_11"] = round(perf_counter() - started, 6)

    payload.update({
        "version": VERSION,
        "source_reliability": source_patch,
        "ci_light_v21_8_5": steps["ci_light_v21_8_5"],
        "steps": steps,
        "timings_seconds": timings,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "t1_t2_scope_changed": False,
        "real_orders_enabled": False,
        "generated_at_v21_15_11_utc": datetime.now(timezone.utc).isoformat(),
    })
    audit_patch = {
        "runtime_version": VERSION,
        "source_reliability": source_patch,
        "ci_light_v21_8_5": steps["ci_light_v21_8_5"],
    }
    for name in ("DAILY_CI_RESTITUTION_V21_15_7.json", "CI_EXPLAINABILITY_AUDIT.json"):
        _patch_json(root / "outputs" / "audit" / name, audit_patch)
    impl.impl._write_final_audit(root, payload)
    return payload


base = impl.base
collection = impl.collection
etf_replay = impl.etf_replay
wave3_cpu = impl.wave3_cpu
refresh_earnings_clock = impl.refresh_earnings_clock
tactical = impl.tactical


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
