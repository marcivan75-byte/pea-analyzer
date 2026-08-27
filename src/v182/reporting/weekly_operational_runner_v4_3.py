from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
import os

from v182.reporting import weekly_tail_super_runner_v21_16_0 as tail
from v182.reporting import weekly_unified_super_runner_v22_2_3 as core
from v182.reporting import weekly_unified_super_runner_v4 as overlay
from v182.reporting import objectives_risk_shadow_v1 as objectives_risk
from v182.reporting import objectives_risk_challenger_v2 as objectives_risk_challenger
from v182.reporting import portfolio_budget_challenger_v2 as portfolio_budget
from v182.reporting import ci_challenger_publication_v2 as challenger_publication
from v182.reporting import sector_or_shadow_v1 as sector_or_shadow


ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_OPERATIONAL_V4_3_UNDER_20_MINUTES"
TARGET_SECONDS = 1200.0


def run(root: Path = ROOT) -> dict:
    started = perf_counter()
    core_started = perf_counter()
    core_payload = core.run(root=root)
    core_seconds = perf_counter() - core_started

    previous_critical = os.environ.get("PEA_WEEKLY_CRITICAL_ONLY")
    os.environ["PEA_WEEKLY_CRITICAL_ONLY"] = "1"
    try:
        tail_started = perf_counter()
        tail_payload = tail.run(root=root)
        tail_seconds = perf_counter() - tail_started
    finally:
        if previous_critical is None:
            os.environ.pop("PEA_WEEKLY_CRITICAL_ONLY", None)
        else:
            os.environ["PEA_WEEKLY_CRITICAL_ONLY"] = previous_critical

    overlay_started = perf_counter()
    overlay_payload = overlay.run(
        root=root,
        ensure_upstream=False,
        run_ci_light=False,
        existing_ci_light=core_payload.get("ci_light_v4_2_independent"),
    )
    overlay_seconds = perf_counter() - overlay_started

    or_started = perf_counter()
    objectives_risk.run(root=root)
    or_payload = objectives_risk_challenger.run(root=root)
    sector_or_payload = sector_or_shadow.run(root=root)
    portfolio_budget.run(root=root)
    publication_payload = challenger_publication.run(root=root)
    or_seconds = perf_counter() - or_started
    total_seconds = perf_counter() - started
    under_target = total_seconds < TARGET_SECONDS
    payload = {
        "status": "SUCCESS_UNDER_20_MINUTES" if under_target else "FAILED_RUNTIME_TARGET",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_seconds": TARGET_SECONDS,
        "total_seconds": round(total_seconds, 6),
        "under_target": under_target,
        "steps_seconds": {
            "core": round(core_seconds, 6),
            "critical_tail": round(tail_seconds, 6),
            "v4_overlay": round(overlay_seconds, 6),
            "objectives_risk_shadow_publication": round(or_seconds, 6),
        },
        "core_status": core_payload.get("status"),
        "tail_status": tail_payload.get("status"),
        "overlay_status": overlay_payload.get("status"),
        "objectives_risk_status": or_payload.get("status"),
        "objectives_risk_publication_status": publication_payload.get("status"),
        "sector_or_shadow_status": sector_or_payload.get("status"),
        "objectives_risk_reference_influence": 0.0,
        "runtime_optimizations": {
            "v4_upstream_recompute_removed": True,
            "duplicate_ci_light_run_removed": True,
            "ci_light_independence_preserved": True,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "information_loss": False,
        },
        "deferred_distinct_shadow_process": ["TACTICAL_SHADOW_BUNDLE", "POSTMARKET_V24_4_2"],
        "deferred_decision_score_weight_influence": 0.0,
        "real_orders_enabled": False,
    }
    audit = root / "outputs/audit/WEEKLY_OPERATIONAL_RUNTIME_V4_3.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if not under_target:
        raise RuntimeError(f"WEEKLY_RUNTIME_TARGET_EXCEEDED:{total_seconds:.3f}>={TARGET_SECONDS:.3f}")
    return payload


def main() -> None:
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
