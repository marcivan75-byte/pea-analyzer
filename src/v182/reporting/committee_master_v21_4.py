from __future__ import annotations

from pathlib import Path
import json
import math
import pandas as pd

from v182.decision.committee_master import load_registry, decisions_from_scores, sector_ranking
from v182.decision.source_confirmation_gate_v21_16 import apply_source_confirmation_gate, source_gate_summary
from v182.reporting import committee_master_run
from v182.reporting.selected_source_enrichment import _read_contract, attach_master_identity, enrich_selected_rows
from v182.reporting.sector_rotation_v2_committee_bridge import build_committee_sector_rotation_v2_status
from v182.risk import entry_exit_governance_v21_8

ROOT = Path(__file__).resolve().parents[3]
HORIZONS = ["CT", "MT", "SHORT", "TOP_DOWN"]


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _key(frame: pd.DataFrame) -> pd.Series:
    return frame["horizon"].astype(str) + "|" + frame["isin"].astype(str)


def _source_invariant_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [c for c in ("asset_class", "horizon", "isin", "decision", "score") if c in frame]
    return frame[columns].copy().reset_index(drop=True)


def _assert_source_layer_non_mutating(before: pd.DataFrame, after: pd.DataFrame) -> None:
    after_core = _source_invariant_snapshot(after)
    if len(before) != len(after_core) or list(before.columns) != list(after_core.columns):
        raise RuntimeError("SOURCE_LAYER_MUTATED_COMMITTEE_SHAPE")
    text_cols = [c for c in ("asset_class", "horizon", "isin", "decision") if c in before]
    for column in text_cols:
        if not before[column].fillna("").astype(str).equals(after_core[column].fillna("").astype(str)):
            raise RuntimeError(f"SOURCE_LAYER_MUTATED_{column.upper()}")
    if "score" in before:
        left = pd.to_numeric(before["score"], errors="coerce"); right = pd.to_numeric(after_core["score"], errors="coerce")
        same = (left.isna() & right.isna()) | ((left - right).abs() <= 1e-12)
        if not bool(same.all()):
            raise RuntimeError("SOURCE_LAYER_MUTATED_SCORE")


def _source_validation_export(decisions: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "asset_class", "horizon", "isin", "name", "decision", "score",
        "source_validation_state", "source_validation_reasons", "source_fully_validated", "ci_source_eligible",
        "boursorama_priority_ready", "boursorama_context_coverage_pct", "boursorama_dynamic_age_hours",
        "boursorama_etf_dynamic_age_hours", "boursorama_source_urls", "boursorama_latest_collected_at",
        "investing_required_timeframe", "investing_required_state", "investing_horizon_signal", "investing_timing_confirmed",
        "investing_daily_signal", "investing_weekly_signal", "investing_monthly_signal", "investing_age_hours",
        "investing_source_urls", "investing_latest_collected_at",
    ]
    existing = [c for c in columns if c in decisions]
    result = decisions[existing].copy()
    if "source_validation_state" in result:
        result = result[result["source_validation_state"].astype(str).ne("NOT_APPLICABLE")]
    return result


def run(root: Path = ROOT) -> dict:
    """Final Action reference decisions plus post-selection source confirmation.

    Boursorama/Investing may qualify readiness, but the source layer is forbidden
    from changing the internal score or decision. A hard invariant check enforces
    that separation before the canonical Committee file is persisted.
    """
    summary = committee_master_run.run(root)
    outdir = root/"outputs"/"committee_master"; decisions_path = outdir/"COMMITTEE_DECISIONS.csv"; actions_path = root/"outputs"/"V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"; etfs_path = root/"outputs"/"V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not decisions_path.exists() or not actions_path.exists():
        summary["action_dual_track"] = {"status": "BLOCKED_INPUT"}; summary["sector_rotation_v2"] = build_committee_sector_rotation_v2_status(root); summary["entry_exit_v21_8"] = {"status": "BLOCKED_INPUT", "decision_influence": 0.0, "real_orders_enabled": False}; return summary
    decisions = _read(decisions_path); actions = _read(actions_path); etfs = _read(etfs_path) if etfs_path.exists() else pd.DataFrame()
    reference_reg = load_registry(root/"config"/"V21_ACTIONS_REFERENCE_V21_0.json"); challenger_reg = load_registry(root/"config"/"V21_ACTIONS_CRITERIA_REGISTRY.json"); reference = decisions_from_scores(actions, reference_reg, "ACTION", HORIZONS); reference["key"] = _key(reference); refmap = reference.set_index("key", drop=False)

    action_mask = (decisions["asset_class"].astype(str) == "ACTION") & decisions["horizon"].astype(str).isin(HORIZONS); actions_current = decisions[action_mask].copy(); actions_current["key"] = _key(actions_current); rows = []
    for idx, row in actions_current.iterrows():
        key = row["key"]
        if key not in refmap.index: continue
        ref = refmap.loc[key]; ref = ref.iloc[0] if isinstance(ref, pd.DataFrame) else ref; challenger_score = pd.to_numeric(pd.Series([row.get("score")]), errors="coerce").iloc[0]; challenger_cov = pd.to_numeric(pd.Series([row.get("coverage_pct")]), errors="coerce").iloc[0]
        decisions.at[idx, "action_challenger_score"] = challenger_score; decisions.at[idx, "action_challenger_coverage_pct"] = challenger_cov; decisions.at[idx, "action_challenger_status"] = row.get("status"); decisions.at[idx, "action_challenger_decision"] = row.get("decision"); decisions.at[idx, "action_challenger_version"] = challenger_reg.get("version"); decisions.at[idx, "action_reference_score"] = ref.get("score"); decisions.at[idx, "action_reference_coverage_pct"] = ref.get("coverage_pct"); decisions.at[idx, "action_reference_status"] = ref.get("status"); decisions.at[idx, "action_reference_decision"] = ref.get("decision"); decisions.at[idx, "action_reference_version"] = reference_reg.get("version")
        decisions.at[idx, "action_score_delta_challenger_vs_reference"] = (challenger_score - float(ref.get("score"))) if pd.notna(challenger_score) and pd.notna(ref.get("score")) else None
        decisions.at[idx, "score"] = ref.get("score"); decisions.at[idx, "coverage_pct"] = ref.get("coverage_pct"); decisions.at[idx, "status"] = ref.get("status"); decisions.at[idx, "decision"] = ref.get("decision"); decisions.at[idx, "active_criteria"] = ref.get("active_criteria"); decisions.at[idx, "available_criteria"] = ref.get("available_criteria"); decisions.at[idx, "score_source"] = "V21.0_REFERENCE_WEIGHTS_ON_1829_UNIVERSE"
        note = str(decisions.at[idx, "notes"] if "notes" in decisions.columns and pd.notna(decisions.at[idx, "notes"]) else ""); decisions.at[idx, "notes"] = (note + " | FINAL ACTION DECISION uses frozen V21.0 reference weights; V21.4 enriched score and 52w/rotation overlays are challenger-only pending PIT/OOS validation.").strip(" |")
        rows.append({"key": key, "isin": row.get("isin"), "name": row.get("name"), "sector": row.get("sector"), "horizon": row.get("horizon"), "reference_score": ref.get("score"), "reference_coverage_pct": ref.get("coverage_pct"), "reference_decision": ref.get("decision"), "challenger_score": challenger_score, "challenger_coverage_pct": challenger_cov, "challenger_decision": row.get("decision"), "challenger_52w_score": row.get("action_52w_challenger_score"), "challenger_52w_decision": row.get("action_52w_challenger_decision"), "delta_score": (challenger_score - float(ref.get("score"))) if pd.notna(challenger_score) and pd.notna(ref.get("score")) else None})

    comparison = pd.DataFrame(rows); comparison.to_csv(outdir/"ACTION_REFERENCE_VS_CHALLENGER_V21_4.csv", sep=";", index=False, encoding="utf-8-sig")

    # The final reference decisions are now frozen. Only after this point may the
    # source layer enrich the shortlist. The invariant snapshot proves it cannot
    # alter score/decision while adding readiness fields.
    before_sources = _source_invariant_snapshot(decisions)
    source_input = attach_master_identity(decisions, actions, etfs)
    decisions, source_context = enrich_selected_rows(source_input, root, profile="WEEKLY_COMMITTEE", network_policy="LIVE_IF_DUE", persist_outputs=True)
    decisions = apply_source_confirmation_gate(decisions, _read_contract(root)); _assert_source_layer_non_mutating(before_sources, decisions); gate_summary = source_gate_summary(decisions)
    source_validation = _source_validation_export(decisions); source_validation.to_csv(outdir/"COMMITTEE_SOURCE_VALIDATION.csv", sep=";", index=False, encoding="utf-8-sig")
    decisions.to_csv(decisions_path, sep=";", index=False, encoding="utf-8-sig")

    sector_ranking(decisions).to_csv(outdir/"SECTOR_RANKING.csv", sep=";", index=False, encoding="utf-8-sig")
    challenger_view = decisions.copy(); mask = (challenger_view["asset_class"].astype(str) == "ACTION") & challenger_view["horizon"].astype(str).isin(HORIZONS); challenger_view.loc[mask, "score"] = challenger_view.loc[mask, "action_challenger_score"]; challenger_view.loc[mask, "coverage_pct"] = challenger_view.loc[mask, "action_challenger_coverage_pct"]; challenger_view.loc[mask, "status"] = challenger_view.loc[mask, "action_challenger_status"]; challenger_view.loc[mask, "decision"] = challenger_view.loc[mask, "action_challenger_decision"]; sector_ranking(challenger_view).to_csv(outdir/"SECTOR_RANKING_CHALLENGER_V21_4.csv", sep=";", index=False, encoding="utf-8-sig")

    v21_8_status = entry_exit_governance_v21_8.run(root)
    if v21_8_status.get("status") != "SUCCESS": raise RuntimeError(f"V21_8_ENTRY_EXIT_FAILED:{v21_8_status.get('status')}")
    divergences = int((comparison["reference_decision"].astype(str) != comparison["challenger_decision"].astype(str)).sum()) if not comparison.empty else 0; ref_buy = int((comparison["reference_decision"].astype(str) == "BUY_CANDIDATE").sum()) if not comparison.empty else 0; chal_buy = int((comparison["challenger_decision"].astype(str) == "BUY_CANDIDATE").sum()) if not comparison.empty else 0
    summary["action_dual_track"] = {"status": "ACTIVE_REFERENCE_PLUS_SHADOW_CHALLENGER", "reference_version": reference_reg.get("version"), "challenger_version": challenger_reg.get("version"), "final_decision_source": "REFERENCE", "comparison_rows": int(len(comparison)), "decision_divergences": divergences, "reference_buy_count": ref_buy, "challenger_buy_count": chal_buy, "performance_attribution": "NONE_TO_V21_4_CHALLENGER_UNTIL_DEDICATED_PIT_OOS_BACKTEST"}
    summary["selected_source_context"] = source_context; summary["source_confirmation_gate"] = gate_summary; summary["source_network_policy"] = "LIVE_IF_DUE"; summary["source_layer_invariant"] = {"score_mutation": False, "decision_mutation": False, "row_mutation": False}
    summary["sector_rotation_v2"] = build_committee_sector_rotation_v2_status(root); summary["entry_exit_v21_8"] = v21_8_status; summary["status_counts"] = decisions.groupby(["asset_class", "horizon", "status"], dropna=False).size().reset_index(name="count").to_dict("records"); summary["decision_counts"] = decisions.groupby(["asset_class", "horizon", "decision"], dropna=False).size().reset_index(name="count").to_dict("records")
    summary.setdefault("outputs", {})["action_reference_vs_challenger"] = "outputs/committee_master/ACTION_REFERENCE_VS_CHALLENGER_V21_4.csv"; summary["outputs"]["committee_source_validation"] = "outputs/committee_master/COMMITTEE_SOURCE_VALIDATION.csv"; summary["outputs"]["source_observations"] = "outputs/source_context/WEEKLY_COMMITTEE_SOURCE_OBSERVATIONS.csv"; summary["outputs"]["source_failures"] = "outputs/source_context/WEEKLY_COMMITTEE_SOURCE_FAILURES.csv"; summary["outputs"]["sector_ranking_challenger"] = "outputs/committee_master/SECTOR_RANKING_CHALLENGER_V21_4.csv"; summary["outputs"]["sector_rotation_v2_shadow"] = "outputs/sector_rotation/V2_SECTOR_ROTATION_SHADOW.csv"; summary["outputs"]["sector_rotation_v2_pit_oos_status"] = "outputs/audit/V2_SECTOR_ROTATION_PIT_OOS_STATUS.json"; summary["outputs"]["sector_rotation_v2_pit_oos_observations"] = "outputs/sector_rotation/V2_PIT_OOS_OBSERVATIONS.csv"; summary["outputs"]["sector_rotation_v2_pit_oos_metrics"] = "outputs/sector_rotation/V2_PIT_OOS_SNAPSHOT_METRICS.csv"; summary["outputs"]["entry_exit_v21_8"] = "outputs/committee_master/V21_8_ENTRY_EXIT_CHALLENGER.csv"; summary["outputs"]["entry_exit_v21_8_audit"] = "outputs/audit/V21_8_ENTRY_EXIT_GOVERNANCE.json"
    summary.setdefault("notes", []).append("Actions use V21.0 frozen weights as final reference decision; V21.4 enriched scores remain challenger-only until PIT/OOS validation."); summary["notes"].append("Boursorama + Investing are applied strictly post-selection. TCT requires Daily STRONG_BUY, CT Weekly STRONG_BUY, MT Monthly STRONG_BUY for FULLY_VALIDATED source readiness. Internal score/decision are invariant."); summary["notes"].append("CI source eligibility requires BUY_CANDIDATE + Boursorama priority-ready + fresh Investing STRONG_BUY on the horizon. WATCH/REVIEW remain visible as waitlist/context, never as fully validated recommendations."); summary["notes"].append("Sector Rotation V2 remains SHADOW diagnostics with zero final-decision influence."); summary["notes"].append("V21.8 remains the entry/exit support baseline: no fixed take-profit, no legacy fixed stop, T2 required for TCT entry, no real orders.")
    (outdir/"SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"); return summary
