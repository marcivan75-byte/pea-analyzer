from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import logging
import os

from v182.reporting import run as enrichment_run
from v182.reporting import (
    android_risk_control_center,
    committee_master_v21_4,
    committee_performance_v21_4,
    etf_mt_v2081_run,
    etf_structure_refresh,
    ipo_dd_gaps_run,
    sector_rotation_v2_shadow_run,
)
from v182.decision import gold_v1_1, ipo_outcomes_v1_2
from v182.decision import ipo_radar_v1_2 as ipo_radar_v1
from v182.risk import beta_correlation_engine

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
SOFTWARE_VERSION = "21.7.4"
PROCESS_VERSION = "UNIFIED_V21_7_4_SECTOR_ROTATION_V2_PIT_OOS_RISK_V1_1_CONTEXT_ONLY"


def _safe_step(name: str, func) -> dict:
    try:
        result = func()
        result = dict(result.__dict__) if hasattr(result, "__dict__") else result
        return {"status": "SUCCESS", "result": result}
    except Exception as exc:
        logger.exception("Unified runner step %s failed; independent steps continue", name)
        return {"status": "FAILED", "error": type(exc).__name__, "detail": str(exc)[:500]}


def _skip_dependency(reason: str) -> dict:
    return {"status": "SKIPPED_DEPENDENCY", "reason": reason}


def _exit_code(payload: dict) -> int:
    return 0 if payload.get("status") == "SUCCESS" else 1


def _sector_validation_from_step(step: dict) -> dict:
    if step.get("status") != "SUCCESS":
        return {"status": "UNAVAILABLE", "promotion_ready": False, "decision_influence": 0.0}
    result = step.get("result") if isinstance(step.get("result"), dict) else {}
    validation = result.get("pit_oos_validation") if isinstance(result.get("pit_oos_validation"), dict) else {}
    if not validation:
        return {"status": "WAIT_FOR_PIT_HISTORY", "promotion_ready": False, "decision_influence": 0.0}
    return validation


def run(root: Path = ROOT) -> dict:
    outdir = root / "outputs" / "unified"
    outdir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    steps: dict[str, dict] = {}

    steps["refresh"] = _safe_step("refresh", enrichment_run.run)
    steps["etf_structure"] = _safe_step("etf_structure", lambda: etf_structure_refresh.run(root))
    steps["etf_mt"] = _safe_step("etf_mt", etf_mt_v2081_run.run)
    steps["gold"] = _safe_step("gold", lambda: gold_v1_1.run(root, os.environ.get("FRED_API_KEY")))
    steps["ipo_radar"] = _safe_step("ipo_radar", lambda: ipo_radar_v1.run(root))

    if steps["ipo_radar"]["status"] == "SUCCESS":
        steps["ipo_dd_gaps"] = _safe_step("ipo_dd_gaps", lambda: ipo_dd_gaps_run.run(root))
        steps["ipo_outcomes"] = _safe_step("ipo_outcomes", lambda: ipo_outcomes_v1_2.run(root))
    else:
        steps["ipo_dd_gaps"] = _skip_dependency(
            "Requires SUCCESS current IPO Radar ranking before due-diligence worklist generation."
        )
        steps["ipo_outcomes"] = _skip_dependency(
            "Requires SUCCESS current IPO Radar snapshot before post-listing outcome attribution."
        )

    if steps["refresh"]["status"] == "SUCCESS":
        steps["sector_rotation_v2"] = _safe_step(
            "sector_rotation_v2", lambda: sector_rotation_v2_shadow_run.run(root)
        )
        steps["committee"] = _safe_step("committee", lambda: committee_master_v21_4.run(root))
    else:
        steps["sector_rotation_v2"] = _skip_dependency(
            "Requires SUCCESS current refresh; stale Action masters are forbidden for Sector Rotation V2 shadow scoring."
        )
        steps["committee"] = _skip_dependency(
            "Requires SUCCESS current refresh; stale or legacy Action masters are forbidden for Committee decisions."
        )

    if steps["committee"]["status"] == "SUCCESS":
        steps["risk_context"] = _safe_step(
            "risk_context", lambda: beta_correlation_engine.run(root)
        )
        steps["risk_control_center"] = _safe_step(
            "risk_control_center", lambda: android_risk_control_center.run(root)
        )
    else:
        steps["risk_context"] = _skip_dependency(
            "Requires SUCCESS current Committee decisions. Risk V1.1 never operates on stale decisions."
        )
        steps["risk_control_center"] = _skip_dependency(
            "Requires current Committee/Risk context before publishing the mobile risk panel."
        )

    if steps["refresh"]["status"] == "SUCCESS" and steps["committee"]["status"] == "SUCCESS":
        steps["performance"] = _safe_step(
            "performance", lambda: committee_performance_v21_4.run(root)
        )
    else:
        steps["performance"] = _skip_dependency(
            "Requires SUCCESS refresh and SUCCESS current Committee; stale decisions are forbidden for virtual transactions."
        )

    outputs = {
        "decisions": "outputs/committee_master/COMMITTEE_DECISIONS.csv",
        "committee_summary": "outputs/committee_master/SUMMARY.json",
        "sector_ranking": "outputs/committee_master/SECTOR_RANKING.csv",
        "sector_ranking_challenger": "outputs/committee_master/SECTOR_RANKING_CHALLENGER_V21_4.csv",
        "action_reference_vs_challenger": "outputs/committee_master/ACTION_REFERENCE_VS_CHALLENGER_V21_4.csv",
        "criteria_coverage": "outputs/committee_master/CRITERIA_COVERAGE.csv",
        "effective_weights": "outputs/committee_master/EFFECTIVE_WEIGHTS_100.xlsx",
        "tct_baseline": "outputs/committee_master/TCT_BASELINE_V24_1_8.csv",
        "tct_shadow": "outputs/committee_master/TCT_SHADOW_V24_1_7.csv",
        "collection_audit_latest": "outputs/data_audit/COLLECTION_DATA_AVAILABILITY_LATEST.xlsx",
        "provenance": "state/provenance/OBSERVATION_PROVENANCE.csv",
        "sector_rotation_v1": "outputs/V21_3_SECTOR_ROTATION.csv",
        "sector_rotation_v2": "outputs/sector_rotation/V2_SECTOR_ROTATION_SHADOW.csv",
        "sector_rotation_v2_audit": "outputs/audit/V2_SECTOR_ROTATION_SHADOW.json",
        "sector_rotation_v2_pit_oos_status": "outputs/audit/V2_SECTOR_ROTATION_PIT_OOS_STATUS.json",
        "sector_rotation_v2_pit_oos_observations": "outputs/sector_rotation/V2_PIT_OOS_OBSERVATIONS.csv",
        "sector_rotation_v2_pit_oos_metrics": "outputs/sector_rotation/V2_PIT_OOS_SNAPSHOT_METRICS.csv",
        "sector_rotation_v2_history": "state/sector_rotation_v2/SECTOR_ROTATION_V2_HISTORY.csv",
        "sector_rotation_v2_constituents": "state/sector_rotation_v2/SECTOR_ROTATION_V2_CONSTITUENTS.csv",
        "risk_audit": "outputs/audit/BETA_CORRELATION_RISK_ENGINE.json",
        "risk_rows": "outputs/risk/BETA_CORRELATION_RISK_ROWS.csv",
        "risk_portfolio": "outputs/risk/PORTFOLIO_RISK_SUMMARY.json",
        "risk_sector_overlay": "outputs/risk/SECTOR_BETA_RISK_OVERLAY.csv",
        "risk_mobile": "outputs/mobile/RISK_V1_1_CONTROL_CENTER.md",
        "performance_workbook": "outputs/performance/COMMITTEE_BUY_PERFORMANCE.xlsx",
        "etf_mt_ranking": "outputs/etf_mt_v2081/V20.8.1_ETF_MT_RANKING.csv",
        "etf_mt_summary": "outputs/etf_mt_v2081/V20.8.1_ETF_MT_SUMMARY.json",
        "gold_decision": "outputs/gold_v1_1/GOLD_V1_1_DECISION.json",
        "gold_criteria": "outputs/gold_v1_1/GOLD_V1_1_CRITERIA.csv",
        "gold_sources": "outputs/gold_v1_1/GOLD_V1_1_SOURCE_STATUS.csv",
        "ipo_ranking": "outputs/ipo_radar/IPO_RANKING.csv",
        "ipo_summary": "outputs/ipo_radar/IPO_SUMMARY.json",
        "ipo_sources": "outputs/ipo_radar/IPO_SOURCE_STATUS.csv",
        "ipo_sec_dd": "outputs/ipo_radar/IPO_SEC_DD_STATUS.csv",
        "ipo_alerts": "outputs/ipo_radar/IPO_ALERTS.csv",
        "ipo_committee_brief": "outputs/ipo_radar/IPO_COMMITTEE_BRIEF.json",
        "ipo_deep_dd_evidence": "outputs/ipo_radar/IPO_DEEP_DD_EVIDENCE.csv",
        "ipo_deep_dd_brief": "outputs/ipo_radar/IPO_DEEP_DD_BRIEF.json",
        "ipo_dd_gaps": "outputs/ipo_radar/IPO_DD_GAPS.csv",
        "ipo_validation": "outputs/ipo_radar/IPO_VALIDATION_STATUS.json",
        "ipo_calibration": "outputs/ipo_radar/IPO_CALIBRATION_STATUS.json",
        "ipo_outcomes": "state/ipo_radar/IPO_OUTCOMES.csv",
    }
    existing = {key: value for key, value in outputs.items() if (root / value).exists()}
    failed = [key for key, value in steps.items() if value["status"] == "FAILED"]
    skipped = [key for key, value in steps.items() if value["status"].startswith("SKIPPED")]
    overall = "SUCCESS" if not failed and not skipped else "PARTIAL_SUCCESS"
    sector_validation = _sector_validation_from_step(steps["sector_rotation_v2"])

    decision_tracks = {
        "actions_final": "V21.0 frozen-weight reference on current 1829 universe",
        "actions_challenger": "V21.4 enriched shadow challenger",
        "etf_mt_reference": "V20.8.1 exact 38-PIT core",
        "etf_mt_challenger": "V20.8.2 missing-data dynamic shadow",
        "tct": "V24.1.8 baseline + exact V24.1.7 T1/T2 shadow",
        "gold": "V1.1 shadow",
        "ipo": "IPO_RADAR_V1.2 deep-DD + PIT-safe outcome calibration shadow/advisory; no automatic BUY",
        "sector_rotation": "V1 baseline + V2.0 multi-factor shadow + locked PIT/OOS validator; Committee diagnostic bridge active; V2 decision influence = 0",
        "risk_context": "RISK_V1.1 robust beta/correlation/diversification context-only; score/decision/sizing/stop influence = 0",
    }

    payload = {
        "version": PROCESS_VERSION,
        "software_version": SOFTWARE_VERSION,
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": overall,
        "live_orders_enabled": False,
        "steps": steps,
        "persisted_outputs": existing,
        "decision_tracks": decision_tracks,
        "sector_rotation_v2_validation": sector_validation,
        "governance": [
            "Runtime/software version is distinct from model versions; decision_tracks is the authoritative model-version map.",
            "Missing canonical Action ISINs are materialized as identity-only rows; no ticker/name/market data are invented.",
            "New/unvalidated Action factors, including 52-week overlays, remain challenger-only until dedicated PIT/OOS validation.",
            "Sector Rotation V2 is SHADOW_ONLY: it cannot change Action/ETF scores, create BUYs, create SELLs, or emit orders before dedicated PIT/OOS validation.",
            "Sector Rotation V2 is integrated into Committee and unified reporting only as diagnostics: current PIT/OOS status, valuation/correction warnings and frozen evidence are visible with decision influence fixed at zero.",
            "Sector Rotation V2 economic outcomes use frozen constituents and the first trading-session close strictly after the signal date; same-day close execution is forbidden.",
            "Sector Rotation V2 final holdout remains locked and model-version drift cannot be mixed into the registered V2.0 OOS evidence.",
            "Sector Rotation V2 explicitly separates rotation opportunity from valuation/correction risk and publishes PROMISING_BUT_OVERVALUED / NO_CHASE warnings.",
            "RISK V1.1 uses the robust PEA Action equal-weight benchmark V2 with cross-sectional 5/95 winsorization, daily breadth control and fail-closed benchmark QC.",
            "RISK V1.1 is CONTEXT_ONLY: beta, downside beta, R2, stress correlation, overlap and economic-driver concentration cannot change scores or decisions.",
            "Three beta-based sizing hypotheses were rejected OOS; no active risk position multiplier is emitted and stop-loss linkage is forbidden.",
            "RISK V1.1 benchmark failure degrades safely to zero score/decision/sizing/stop influence rather than blocking the Committee.",
            "IPO Radar V1.2 remains SHADOW_ONLY. It preserves V1.1 identity quarantine and due-diligence coverage controls, prefers prospectus Inline XBRL over Company Facts for pre-IPO financial evidence, reconstructs post-IPO balance-sheet quality only when net proceeds are evidenced, and keeps absolute valuation diagnostics separate from the peer-relative valuation criterion.",
            "IPO outcome calibration uses the last evidence snapshot strictly before the actual first trading calendar date; same-day snapshots are excluded to prevent look-ahead.",
            "IPO post-listing performance is measured from the prospectus IPO price when available, otherwise the last pre-listing price-range midpoint, with first-close fallback for calibration only.",
            "IPO score buckets, drawdowns and forward returns are observational evidence only; they cannot reweight or promote the model without a dedicated PIT/OOS audit.",
            "IPO Radar V1.2 can conservatively trigger the existing insufficient-12-month-liquidity hard block only when even the upper-bound post-IPO runway before planned uses of proceeds is below one year.",
            "Every collection publishes retained-value provenance plus missing/partial/available data.",
            "Per-field retained provenance governs evidence/freshness merge decisions and persists across runs.",
            "Dynamic available-criterion weights renormalize to 100% while minimum coverage gates remain active.",
            "Virtual BUYs execute no earlier than the next observed run and one consolidated position is allowed per ISIN.",
            "Virtual performance is model-version cohorted and never consumes stale Committee decisions.",
            "A partial unified run returns a non-zero CLI exit code so GitHub cannot display false green success.",
            "T1/T2 are ACTION TCT only.",
            "ETF MT 90.91% historical OOS attribution belongs only to exact V20.8.1 38-PIT core.",
            "No real orders are emitted.",
        ],
    }
    path = outdir / f"UNIFIED_SUMMARY_{run_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (outdir / "UNIFIED_SUMMARY_LATEST.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    payload = run(Path(args.root))
    raise SystemExit(_exit_code(payload))


if __name__ == "__main__":
    main()
