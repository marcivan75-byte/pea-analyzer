from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import logging
import time

from v182.reporting import run as enrichment_run
from v182.reporting import (
    android_risk_control_center,
    committee_ci_explainability_v21_16,
    committee_master_v21_4,
    etf_mt_v2081_run,
    etf_structure_refresh,
    sector_rotation_v2_decision_context,
    sector_rotation_v2_shadow_run,
)
from v182.risk import beta_correlation_engine
from v182.reporting.runtime_telemetry import write_step_runtime

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
SOFTWARE_VERSION = "21.16.0"
PROCESS_VERSION = "UNIFIED_V21_16_SOURCE_GATED_SCOPE_RATIONALIZED_NO_LT_GOLD_CRYPTO_IPO"


def _safe_step(name: str, func) -> dict:
    wall_start = time.perf_counter(); cpu_start = time.process_time()
    try:
        result = func(); result = dict(result.__dict__) if hasattr(result, "__dict__") else result; payload = {"status": "SUCCESS", "result": result}
    except Exception as exc:
        logger.exception("Unified runner step %s failed; independent steps continue", name); payload = {"status": "FAILED", "error": type(exc).__name__, "detail": str(exc)[:500]}
    payload["wall_seconds"] = round(time.perf_counter() - wall_start, 6); payload["cpu_seconds"] = round(time.process_time() - cpu_start, 6); return payload


def _skip_dependency(reason: str) -> dict:
    return {"status": "SKIPPED_DEPENDENCY", "reason": reason, "wall_seconds": 0.0, "cpu_seconds": 0.0}


def _exit_code(payload: dict) -> int:
    return 0 if payload.get("status") == "SUCCESS" else 1


def _sector_validation_from_step(step: dict) -> dict:
    if step.get("status") != "SUCCESS": return {"status": "UNAVAILABLE", "promotion_ready": False, "decision_influence": 0.0}
    result = step.get("result") if isinstance(step.get("result"), dict) else {}; validation = result.get("pit_oos_validation") if isinstance(result.get("pit_oos_validation"), dict) else {}
    return validation or {"status": "WAIT_FOR_PIT_HISTORY", "promotion_ready": False, "decision_influence": 0.0}


def _primary_etf_cache_ready(root: Path) -> bool:
    cache = root / "data" / "cache" / "etf"; return cache.is_dir() and any(cache.glob("history_*.parquet"))


def _cached_etf_mt(root: Path) -> dict:
    return _safe_step("etf_mt", lambda: etf_mt_v2081_run.run(root, history_cache_dir=root/"data"/"cache"/"etf", refresh_history=False, refresh_if_reuse_cache_missing=True))


def _post_risk_outputs_parallel(root: Path) -> tuple[dict, dict]:
    """Build independent risk-control and read-only CI documents after risk state is frozen."""
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="post-risk-output") as pool:
        risk_control = pool.submit(_safe_step, "risk_control_center", lambda: android_risk_control_center.run(root))
        ci = pool.submit(_safe_step, "ci_explainability", lambda: committee_ci_explainability_v21_16.run(root))
        return risk_control.result(), ci.result()


def run(root: Path = ROOT) -> dict:
    wall_start = time.perf_counter(); cpu_start = time.process_time(); outdir = root/"outputs"/"unified"; outdir.mkdir(parents=True, exist_ok=True); run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ"); steps: dict[str, dict] = {}
    steps["refresh"] = _safe_step("refresh", enrichment_run.run); refresh_ok = steps["refresh"]["status"] == "SUCCESS"; cache_only_etf_mt = refresh_ok and _primary_etf_cache_ready(root)

    if cache_only_etf_mt:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="etf-independent") as pool:
            structure = pool.submit(_safe_step, "etf_structure", lambda: etf_structure_refresh.run(root)); mt = pool.submit(_cached_etf_mt, root); steps["etf_structure"] = structure.result(); steps["etf_mt"] = mt.result()
    else:
        steps["etf_structure"] = _safe_step("etf_structure", lambda: etf_structure_refresh.run(root))
        steps["etf_mt"] = _cached_etf_mt(root) if refresh_ok else _safe_step("etf_mt", lambda: etf_mt_v2081_run.run(root))

    if refresh_ok:
        steps["sector_rotation_v2"] = _safe_step("sector_rotation_v2", lambda: sector_rotation_v2_shadow_run.run(root)); steps["committee"] = _safe_step("committee", lambda: committee_master_v21_4.run(root))
    else:
        steps["sector_rotation_v2"] = _skip_dependency("Requires SUCCESS current refresh; stale Action masters are forbidden for Sector Rotation V2 shadow scoring."); steps["committee"] = _skip_dependency("Requires SUCCESS current refresh; stale or legacy Action masters are forbidden for Committee decisions.")

    if steps["sector_rotation_v2"]["status"] == "SUCCESS" and steps["committee"]["status"] == "SUCCESS":
        steps["sector_rotation_v2_decision_context"] = _safe_step("sector_rotation_v2_decision_context", lambda: sector_rotation_v2_decision_context.run(root))
    else:
        steps["sector_rotation_v2_decision_context"] = _skip_dependency("Requires SUCCESS current Sector Rotation V2 shadow evidence and SUCCESS current Committee decisions.")

    if steps["committee"]["status"] == "SUCCESS":
        steps["risk_context"] = _safe_step("risk_context", lambda: beta_correlation_engine.run(root)); risk_control, ci = _post_risk_outputs_parallel(root); steps["risk_control_center"] = risk_control; steps["ci_explainability"] = ci
    else:
        steps["risk_context"] = _skip_dependency("Requires SUCCESS current Committee decisions. Risk V1.1 never operates on stale decisions."); steps["risk_control_center"] = _skip_dependency("Requires current Committee/Risk context before publishing the mobile risk panel."); steps["ci_explainability"] = _skip_dependency("Requires SUCCESS current Committee decisions before publishing Android/PC Committee explainability.")

    steps["performance"] = {"status": "SKIPPED_GOVERNANCE", "reason": "V21.8 disables legacy fixed-stop risk sizing and virtual execution until a separately validated sizing policy exists.", "wall_seconds": 0.0, "cpu_seconds": 0.0}
    runtime_wall = time.perf_counter() - wall_start; runtime_cpu = time.process_time() - cpu_start; runtime_paths = write_step_runtime(root/"outputs"/"audit", run_id=run_id, profile="WEEKLY_FULL_COMMITTEE", wall_seconds=runtime_wall, cpu_seconds=runtime_cpu, steps=steps)

    outputs = {
        "decisions": "outputs/committee_master/COMMITTEE_DECISIONS.csv",
        "committee_summary": "outputs/committee_master/SUMMARY.json",
        "committee_source_validation": "outputs/committee_master/COMMITTEE_SOURCE_VALIDATION.csv",
        "source_observations": "outputs/source_context/WEEKLY_COMMITTEE_SOURCE_OBSERVATIONS.csv",
        "source_failures": "outputs/source_context/WEEKLY_COMMITTEE_SOURCE_FAILURES.csv",
        "ci_android": "outputs/mobile/ANDROID_CI_CONTROL_CENTER.md",
        "ci_word": "outputs/committee_master/CI_COMITE_INVESTISSEMENT.docx",
        "ci_weighted_reference": "outputs/committee_master/CI_REFERENTIEL_PONDERE.xlsx",
        "ci_source_validation": "outputs/committee_master/CI_VALIDATION_SOURCES.csv",
        "ci_decision_brief_v3": "outputs/decision_brief/CI_DECISION_BRIEF_V3.docx",
        "ci_decision_matrix_v3": "outputs/decision_brief/CI_DECISION_MATRIX_V3.csv",
        "ci_recommendation_basket_risk": "outputs/committee_master/CI_RISQUE_PANIER_RECOMMANDATIONS.docx",
        "ci_explainability_audit": "outputs/audit/CI_EXPLAINABILITY_AUDIT.json",
        "entry_exit_v21_8": "outputs/committee_master/V21_8_ENTRY_EXIT_CHALLENGER.csv",
        "entry_exit_v21_8_audit": "outputs/audit/V21_8_ENTRY_EXIT_GOVERNANCE.json",
        "entry_exit_v21_8_state": "state/provenance/V21_8_ENTRY_EXIT_STATE.csv",
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
        "sector_rotation_v2_decision_context": "outputs/committee_master/COMMITTEE_SECTOR_ROTATION_V2_CONTEXT.csv",
        "sector_rotation_v2_decision_context_audit": "outputs/audit/SECTOR_ROTATION_V2_DECISION_CONTEXT.json",
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
        "etf_mt_ranking": "outputs/etf_mt_v2081/V20.8.1_ETF_MT_RANKING.csv",
        "etf_mt_summary": "outputs/etf_mt_v2081/V20.8.1_ETF_MT_SUMMARY.json",
        "unified_runtime_json": "outputs/audit/UNIFIED_RUNTIME_V21_13_7.json",
        "unified_runtime_csv": "outputs/audit/UNIFIED_RUNTIME_V21_13_7.csv",
    }
    existing = {key: value for key, value in outputs.items() if (root/value).exists()}; failed = [key for key, value in steps.items() if value["status"] == "FAILED"]; skipped_dependencies = [key for key, value in steps.items() if value["status"].startswith("SKIPPED_DEPENDENCY")]; overall = "SUCCESS" if not failed and not skipped_dependencies else "PARTIAL_SUCCESS"; sector_validation = _sector_validation_from_step(steps["sector_rotation_v2"])

    decision_tracks = {
        "actions_final": "V21.0 frozen-weight reference on current 1829 universe",
        "actions_challenger": "V21.4 enriched shadow challenger",
        "post_selection_source_gate": "V21.16 Boursorama priority fiche + Investing STRONG_BUY timing: TCT Daily / CT Weekly / MT Monthly; can WAIT an entry but never mutate score/selection decision",
        "entry_exit": "V21.8 official decision-support baseline + V21.16 source-confirmation WAIT + TCT exact T2 gate; HOLD/PROTECT/EXIT temporal state; no fixed TP/legacy fixed stop/new hard stop",
        "committee_reporting": "CI_RESTITUTION_V21_16 + weighted TCT/Action/ETF reference + explicit fully-validated/pending source split; read-only after canonical Committee",
        "etf_mt_reference": "V20.8.1 exact 38-PIT core",
        "etf_mt_challenger": "V20.8.2 missing-data dynamic shadow; Boursorama/Investing context cache-only until weekly Committee refresh",
        "tct": "V24.1.8 baseline + exact V24.1.7 T1/T2 shadow; T1/T2 ACTION TCT only",
        "sector_rotation": "V1 baseline + V2.0 multi-factor shadow + locked PIT/OOS validator + per-decision Action/ETF diagnostic context; V2 decision influence = 0",
        "risk_context": "RISK_V1.1 robust beta/correlation/diversification context-only; score/decision/sizing/stop influence = 0",
        "virtual_performance": "SKIPPED_GOVERNANCE under V21.8 because legacy sizing depends on invalidated fixed-stop assumptions",
    }

    payload = {
        "version": PROCESS_VERSION, "software_version": SOFTWARE_VERSION, "run_id": run_id, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "status": overall, "live_orders_enabled": False, "steps": steps, "persisted_outputs": existing, "decision_tracks": decision_tracks, "sector_rotation_v2_validation": sector_validation,
        "runtime": {"version": "UNIFIED_RUNTIME_V21_13_7", "wall_seconds": round(runtime_wall, 6), "cpu_seconds": round(runtime_cpu, 6), "paths": runtime_paths, "decision_logic_changed": False, "source_network_ownership_changed": True},
        "governance": [
            "Runtime/software version is distinct from model versions; decision_tracks is the authoritative model-version map.",
            "Missing canonical Action ISINs are materialized as identity-only rows; no ticker/name/market data are invented.",
            "New/unvalidated Action factors, including 52-week overlays, remain challenger-only until dedicated PIT/OOS validation.",
            "V21.16 source confirmation is strictly post-selection: it cannot modify scores, weights, thresholds or Committee selection decisions.",
            "A BUY_CANDIDATE is a fully validated CI recommendation only when Boursorama priority context is sufficiently complete/fresh and Investing is STRONG_BUY on the required horizon: Daily TCT, Weekly CT, Monthly MT.",
            "For selected TCT/CT/MT rows, missing or non-STRONG_BUY source confirmation changes entry readiness to WAIT, never the underlying selection decision.",
            "TCT entry additionally requires exact T2 confirmation; T1 alone never opens an entry and T1/T2 remain ACTION TCT only.",
            "Weekly Committee and daily TCT/CT are the only live owners of the selective Boursorama/Investing refresh; internal Action MT and ETF MT consume the cache only.",
            "Boursorama Actions and ETFs share one provider start-rate limiter; the provider remains capped at one request start per second and four total in-flight workers.",
            "CI explainability is read-only and makes zero external collection calls; it reports source freshness/provenance already frozen in canonical Committee decisions.",
            "V21.8 position context is HOLD -> PROTECT -> EXIT; a first multifactor deterioration produces PROTECT and persistent deterioration on a later run confirms EXIT.",
            "V21.8 temporal state is persisted inside the existing provenance state cache; an explicit emergency flag remains the only direct EMERGENCY_EXIT path.",
            "Profit level and profit giveback are context only and never create a standalone exit signal.",
            "No fixed take-profit, legacy fixed stop, or new hard stop is operational in V21.8; the 7% figure is a research risk ceiling only and gaps/slippage can exceed any stop.",
            "Legacy virtual execution/performance is disabled under V21.8 because its sizing and exit logic depend on invalidated fixed-stop assumptions; historical state is audit-only until a separately validated sizing policy exists.",
            "CI Android, Word and weighted Excel are generated from canonical Committee decisions and cannot mutate scores, decisions, weights, thresholds or order state.",
            "Sector Rotation V2 is SHADOW_ONLY and integrated only as diagnostics with decision influence fixed at zero.",
            "Sector Rotation V2 per-decision context is published in a separate immutable diagnostic file keyed to Committee rows; COMMITTEE_DECISIONS.csv is not modified by that context join.",
            "RISK V1.1 is CONTEXT_ONLY: beta, downside beta, R2, stress correlation, overlap and economic-driver concentration cannot change scores or decisions.",
            "Three beta-based sizing hypotheses were rejected OOS; no active risk position multiplier is emitted and stop-loss linkage is forbidden.",
            "Actions LT, ETF LT, Gold, Crypto/ETP and IPO are removed from the active runtime and cannot emit decisions or outputs.",
            "Every collection publishes retained-value provenance plus missing/partial/available data.",
            "Per-field retained provenance governs evidence/freshness merge decisions and persists across runs.",
            "Dynamic available-criterion weights renormalize to 100% while minimum coverage gates remain active.",
            "A partial unified run returns a non-zero CLI exit code so GitHub cannot display false green success.",
            "ETF MT 90.91% historical OOS attribution belongs only to exact V20.8.1 38-PIT core.",
            "No real orders are emitted.",
        ],
    }
    path = outdir/f"UNIFIED_SUMMARY_{run_id}.json"; path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"); (outdir/"UNIFIED_SUMMARY_LATEST.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"); print(json.dumps(payload, ensure_ascii=False, indent=2, default=str)); return payload


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--root", default=str(ROOT)); args = parser.parse_args(); payload = run(Path(args.root)); raise SystemExit(_exit_code(payload))


if __name__ == "__main__": main()
