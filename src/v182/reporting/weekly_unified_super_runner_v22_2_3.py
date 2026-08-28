from __future__ import annotations

from pathlib import Path
from time import perf_counter
from datetime import datetime, timezone
import json
import os

from v182.reporting import ci_light_v4
from v182.reporting import weekly_unified_super_runner_v22_2_2 as previous

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_3"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_3.json"


def run(root: Path = ROOT) -> dict:
    """Run full weighted CI and the genuinely independent CI LIGHT V4.2 process."""
    started = perf_counter()
    audit_dir = root / "outputs/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    light_payload: dict = {}
    error = None
    ci_seconds = 0.0
    light_seconds = 0.0
    previous_slow_source_mode = os.environ.get("PEA_SLOW_SOURCE_MODE")
    previous_run_id = os.environ.get("V182_RUN_ID")
    os.environ.setdefault("PEA_SLOW_SOURCE_MODE", "CACHE_PREFERRED")
    os.environ.setdefault(
        "V182_RUN_ID", datetime.now(timezone.utc).strftime("weekly-%Y%m%dT%H%M%SZ")
    )
    effective_slow_source_mode = os.environ["PEA_SLOW_SOURCE_MODE"].strip().upper()
    effective_run_id = os.environ["V182_RUN_ID"]
    try:
        ci_started = perf_counter()
        payload = previous.run(root=root)
        ci_seconds = perf_counter() - ci_started
        light_started = perf_counter()
        light_payload = ci_light_v4.run(root=root)
        light_seconds = perf_counter() - light_started
        if light_payload.get("status") not in {"SUCCESS", "NO_UPSTREAM_ROWS"}:
            raise RuntimeError(f"CI_LIGHT_V4_2_FAILED:{light_payload.get('status')}")
        payload = dict(payload)
        payload["ci_light_v4_2_independent"] = light_payload
        payload["ci_seconds"] = round(ci_seconds, 6)
        payload["ci_light_seconds"] = round(light_seconds, 6)
        return payload
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:700]}"
        raise
    finally:
        if previous_slow_source_mode is None:
            os.environ.pop("PEA_SLOW_SOURCE_MODE", None)
        else:
            os.environ["PEA_SLOW_SOURCE_MODE"] = previous_slow_source_mode
        if previous_run_id is None:
            os.environ.pop("V182_RUN_ID", None)
        else:
            os.environ["V182_RUN_ID"] = previous_run_id
        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            "ci_seconds": round(ci_seconds, 6),
            "ci_light_seconds": round(light_seconds, 6),
            "weekly_runtime_target_seconds": 1200,
            "slow_source_mode": effective_slow_source_mode,
            "checkpoint_run_id": effective_run_id,
            "checkpoint_scope_unique_per_weekly_invocation": True,
            "same_day_done_wave_skip_forbidden": True,
            "ordinary_ttl_refresh_deferred": effective_slow_source_mode == "CACHE_PREFERRED",
            "missing_and_hard_stale_refresh_preserved": True,
            "cached_values_and_source_timestamps_preserved": True,
            "full_ci_preserved": True,
            "full_ci_all_weighted_criteria_preserved": True,
            "full_ci_scores_or_decisions_changed_by_light": False,
            "ci_light_status": light_payload.get("status"),
            "ci_light_selected": light_payload.get("selected"),
            "ci_light_selected_by_horizon": light_payload.get("selected_by_horizon"),
            "ci_light_boursorama_recommendation_required": ["ACHETER", "RENFORCER"],
            "ci_light_boursorama_analyst_count_rule": ">10",
            "ci_light_boursorama_upside_rule": ">20%",
            "ci_light_universe_source": "inputs/CI_LIGHT_UNIVERSE_V4.csv",
            "ci_light_uses_ci_output": False,
            "ci_light_reuses_ci_selection_context": False,
            "ci_light_etf_boursorama_exact_fiche_required": True,
            "ci_light_etf_pea_eligibility_displayed_required": True,
            "ci_light_etf_equity_analyst_consensus_required": False,
            "ci_light_morningstar_fallback": "ETF_WEEKLY_OR_MONTHLY_MISSING_ONLY_AND_RATING_GTE_4",
            "ci_light_morningstar_overrides_neutral_or_negative": False,
            "ci_light_tradingview_positive_values": ["BUY", "STRONG_BUY"],
            "ci_light_tradingview_daily_required": True,
            "ci_light_tradingview_weekly_required": True,
            "ci_light_tradingview_monthly_required": True,
            "ci_light_all_three_tradingview_timeframes_required_simultaneously": True,
            "ci_light_boursorama_url_exported": True,
            "ci_light_tradingview_url_exported": True,
            "ci_light_inherits_full_ci_final_score_confidence_gate": False,
            "source_can_create_ci_light_candidate": True,
            "source_can_create_ci_candidate": False,
            "selection_score_changed": False,
            "criteria_changed": True,
            "weights_changed": False,
            "thresholds_changed": True,
            "wave09_disabled": True,
            "t1_t2_scope": "ACTION_TCT_ONLY",
            "t1_t2_formula_version": "T1T2_V3_2026_08_STRICT_SEQUENCE",
            "real_orders_enabled": False,
        }
        (audit_dir / AUDIT_NAME).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(previous.previous.core.previous.previous.previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
