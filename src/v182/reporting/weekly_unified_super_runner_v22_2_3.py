from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json

from v182.reporting import ci_light_v22_2_3
from v182.reporting import weekly_unified_super_runner_v22_2_2 as previous

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_3"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_3.json"


def run(root: Path = ROOT) -> dict:
    """V22.2.3 preserves full weighted CI and adds an independent minimalist CI LIGHT view."""
    started = perf_counter()
    audit_dir = root / "outputs/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    light_payload: dict = {}
    error = None
    try:
        payload = previous.run(root=root)
        light_payload = ci_light_v22_2_3.run(root=root)
        if light_payload.get("status") not in {"SUCCESS", "NO_UPSTREAM_ROWS"}:
            raise RuntimeError(f"CI_LIGHT_V22_2_3_FAILED:{light_payload.get('status')}")
        payload = dict(payload)
        payload["ci_light_v22_2_3"] = light_payload
        return payload
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:700]}"
        raise
    finally:
        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            "full_ci_preserved": True,
            "full_ci_all_weighted_criteria_preserved": True,
            "full_ci_scores_or_decisions_changed_by_light": False,
            "ci_light_status": light_payload.get("status"),
            "ci_light_selected": light_payload.get("selected"),
            "ci_light_selected_by_horizon": light_payload.get("selected_by_horizon"),
            "ci_light_boursorama_recommendation_required": ["ACHETER", "RENFORCER"],
            "ci_light_boursorama_analyst_count_rule": ">10",
            "ci_light_boursorama_upside_rule": ">20%",
            "ci_light_etf_same_boursorama_rules": True,
            "ci_light_morningstar_consensus_substitution": False,
            "ci_light_tradingview_positive_values": ["BUY", "STRONG_BUY"],
            "ci_light_tradingview_daily_required": True,
            "ci_light_tradingview_weekly_required": True,
            "ci_light_tradingview_monthly_required": True,
            "ci_light_all_three_tradingview_timeframes_required_simultaneously": True,
            "ci_light_boursorama_url_exported": True,
            "ci_light_tradingview_url_exported": True,
            "ci_light_inherits_full_ci_final_score_confidence_gate": False,
            "source_can_create_candidate": False,
            "selection_score_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "wave09_disabled": True,
            "t1_t2_scope": "ACTION_TCT_ONLY",
            "real_orders_enabled": False,
        }
        (audit_dir / AUDIT_NAME).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(previous.previous.core.previous.previous.previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
