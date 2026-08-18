from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

from v182.decision.committee_master import load_registry, decisions_from_scores, sector_ranking
from v182.reporting import committee_master_gold_v1_1
from v182.reporting.sector_rotation_v2_committee_bridge import build_committee_sector_rotation_v2_status
from v182.risk import entry_exit_governance_v21_8

ROOT=Path(__file__).resolve().parents[3]
HORIZONS=["CT","MT","LT","SHORT","TOP_DOWN"]


def _read(path:Path)->pd.DataFrame:
    return pd.read_csv(path,sep=";",encoding="utf-8-sig",low_memory=False)


def _key(frame:pd.DataFrame)->pd.Series:
    return frame["horizon"].astype(str)+"|"+frame["isin"].astype(str)


def run(root:Path=ROOT)->dict:
    """Dual-track Action Committee plus V21.8 entry/exit decision support.

    Final Action CT/MT/LT/SHORT/TOP_DOWN selection remains the frozen V21.0-weight
    reference. V21.8 consumes those final Committee decisions in a separate file;
    it never mutates score/decision, never emits an order and never promotes a
    fixed take-profit or fixed hard stop.
    """
    summary=committee_master_gold_v1_1.run(root)
    outdir=root/"outputs"/"committee_master"; decisions_path=outdir/"COMMITTEE_DECISIONS.csv"
    actions_path=root/"outputs"/"V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    if not decisions_path.exists() or not actions_path.exists():
        summary["action_dual_track"]={"status":"BLOCKED_INPUT"}
        summary["sector_rotation_v2"]=build_committee_sector_rotation_v2_status(root)
        summary["entry_exit_v21_8"]={"status":"BLOCKED_INPUT","decision_influence":0.0,"real_orders_enabled":False}
        return summary
    decisions=_read(decisions_path); actions=_read(actions_path)
    reference_reg=load_registry(root/"config"/"V21_ACTIONS_REFERENCE_V21_0.json")
    challenger_reg=load_registry(root/"config"/"V21_ACTIONS_CRITERIA_REGISTRY.json")
    reference=decisions_from_scores(actions,reference_reg,"ACTION",HORIZONS)
    reference["key"]=_key(reference); refmap=reference.set_index("key",drop=False)

    action_mask=(decisions["asset_class"].astype(str)=="ACTION") & decisions["horizon"].astype(str).isin(HORIZONS)
    actions_current=decisions[action_mask].copy(); actions_current["key"]=_key(actions_current); rows=[]
    for idx,row in actions_current.iterrows():
        key=row["key"]
        if key not in refmap.index: continue
        ref=refmap.loc[key]; ref=ref.iloc[0] if isinstance(ref,pd.DataFrame) else ref
        challenger_score=pd.to_numeric(pd.Series([row.get("score")]),errors="coerce").iloc[0]
        challenger_cov=pd.to_numeric(pd.Series([row.get("coverage_pct")]),errors="coerce").iloc[0]
        decisions.at[idx,"action_challenger_score"]=challenger_score
        decisions.at[idx,"action_challenger_coverage_pct"]=challenger_cov
        decisions.at[idx,"action_challenger_status"]=row.get("status")
        decisions.at[idx,"action_challenger_decision"]=row.get("decision")
        decisions.at[idx,"action_challenger_version"]=challenger_reg.get("version")
        decisions.at[idx,"action_reference_score"]=ref.get("score")
        decisions.at[idx,"action_reference_coverage_pct"]=ref.get("coverage_pct")
        decisions.at[idx,"action_reference_status"]=ref.get("status")
        decisions.at[idx,"action_reference_decision"]=ref.get("decision")
        decisions.at[idx,"action_reference_version"]=reference_reg.get("version")
        decisions.at[idx,"action_score_delta_challenger_vs_reference"]=(challenger_score-float(ref.get("score"))) if pd.notna(challenger_score) and pd.notna(ref.get("score")) else None
        decisions.at[idx,"score"]=ref.get("score"); decisions.at[idx,"coverage_pct"]=ref.get("coverage_pct"); decisions.at[idx,"status"]=ref.get("status"); decisions.at[idx,"decision"]=ref.get("decision"); decisions.at[idx,"active_criteria"]=ref.get("active_criteria"); decisions.at[idx,"available_criteria"]=ref.get("available_criteria"); decisions.at[idx,"score_source"]="V21.0_REFERENCE_WEIGHTS_ON_1829_UNIVERSE"
        note=str(decisions.at[idx,"notes"] if "notes" in decisions.columns and pd.notna(decisions.at[idx,"notes"]) else "")
        decisions.at[idx,"notes"]=(note+" | FINAL ACTION DECISION uses frozen V21.0 reference weights; V21.4 enriched score and 52w/rotation overlays are challenger-only pending PIT/OOS validation.").strip(" |")
        rows.append({"key":key,"isin":row.get("isin"),"name":row.get("name"),"sector":row.get("sector"),"horizon":row.get("horizon"),"reference_score":ref.get("score"),"reference_coverage_pct":ref.get("coverage_pct"),"reference_decision":ref.get("decision"),"challenger_score":challenger_score,"challenger_coverage_pct":challenger_cov,"challenger_decision":row.get("decision"),"challenger_52w_score":row.get("action_52w_challenger_score"),"challenger_52w_decision":row.get("action_52w_challenger_decision"),"delta_score":(challenger_score-float(ref.get("score"))) if pd.notna(challenger_score) and pd.notna(ref.get("score")) else None})

    comparison=pd.DataFrame(rows); comparison.to_csv(outdir/"ACTION_REFERENCE_VS_CHALLENGER_V21_4.csv",sep=";",index=False,encoding="utf-8-sig")
    decisions.to_csv(decisions_path,sep=";",index=False,encoding="utf-8-sig")
    sector_ranking(decisions).to_csv(outdir/"SECTOR_RANKING.csv",sep=";",index=False,encoding="utf-8-sig")
    challenger_view=decisions.copy(); mask=(challenger_view["asset_class"].astype(str)=="ACTION") & challenger_view["horizon"].astype(str).isin(HORIZONS)
    challenger_view.loc[mask,"score"]=challenger_view.loc[mask,"action_challenger_score"]
    challenger_view.loc[mask,"coverage_pct"]=challenger_view.loc[mask,"action_challenger_coverage_pct"]
    challenger_view.loc[mask,"status"]=challenger_view.loc[mask,"action_challenger_status"]
    challenger_view.loc[mask,"decision"]=challenger_view.loc[mask,"action_challenger_decision"]
    sector_ranking(challenger_view).to_csv(outdir/"SECTOR_RANKING_CHALLENGER_V21_4.csv",sep=";",index=False,encoding="utf-8-sig")

    v21_8_status=entry_exit_governance_v21_8.run(root)
    if v21_8_status.get("status") != "SUCCESS":
        raise RuntimeError(f"V21_8_ENTRY_EXIT_FAILED:{v21_8_status.get('status')}")

    divergences=int((comparison["reference_decision"].astype(str)!=comparison["challenger_decision"].astype(str)).sum()) if not comparison.empty else 0
    ref_buy=int((comparison["reference_decision"].astype(str)=="BUY_CANDIDATE").sum()) if not comparison.empty else 0
    chal_buy=int((comparison["challenger_decision"].astype(str)=="BUY_CANDIDATE").sum()) if not comparison.empty else 0
    summary["action_dual_track"]={"status":"ACTIVE_REFERENCE_PLUS_SHADOW_CHALLENGER","reference_version":reference_reg.get("version"),"challenger_version":challenger_reg.get("version"),"final_decision_source":"REFERENCE","comparison_rows":int(len(comparison)),"decision_divergences":divergences,"reference_buy_count":ref_buy,"challenger_buy_count":chal_buy,"performance_attribution":"NONE_TO_V21_4_CHALLENGER_UNTIL_DEDICATED_PIT_OOS_BACKTEST"}
    summary["sector_rotation_v2"]=build_committee_sector_rotation_v2_status(root)
    summary["entry_exit_v21_8"]=v21_8_status
    summary["status_counts"]=decisions.groupby(["asset_class","horizon","status"],dropna=False).size().reset_index(name="count").to_dict("records")
    summary["decision_counts"]=decisions.groupby(["asset_class","horizon","decision"],dropna=False).size().reset_index(name="count").to_dict("records")
    summary.setdefault("outputs",{})["action_reference_vs_challenger"]="outputs/committee_master/ACTION_REFERENCE_VS_CHALLENGER_V21_4.csv"
    summary["outputs"]["sector_ranking_challenger"]="outputs/committee_master/SECTOR_RANKING_CHALLENGER_V21_4.csv"
    summary["outputs"]["sector_rotation_v2_shadow"]="outputs/sector_rotation/V2_SECTOR_ROTATION_SHADOW.csv"
    summary["outputs"]["sector_rotation_v2_pit_oos_status"]="outputs/audit/V2_SECTOR_ROTATION_PIT_OOS_STATUS.json"
    summary["outputs"]["sector_rotation_v2_pit_oos_observations"]="outputs/sector_rotation/V2_PIT_OOS_OBSERVATIONS.csv"
    summary["outputs"]["sector_rotation_v2_pit_oos_metrics"]="outputs/sector_rotation/V2_PIT_OOS_SNAPSHOT_METRICS.csv"
    summary["outputs"]["entry_exit_v21_8"]="outputs/committee_master/V21_8_ENTRY_EXIT_CHALLENGER.csv"
    summary["outputs"]["entry_exit_v21_8_audit"]="outputs/audit/V21_8_ENTRY_EXIT_GOVERNANCE.json"
    summary.setdefault("notes",[]).append("Actions use V21.0 frozen weights as final reference decision; V21.4 enriched scores and unvalidated positive/negative 52w overlays are challenger-only until PIT/OOS validation.")
    summary["notes"].append("Sector Rotation V2 is integrated into Committee reporting as SHADOW diagnostics only: PIT/OOS status, valuation/correction warnings and frozen-outcome evidence are visible, with zero influence on final Action/ETF decisions until governed promotion.")
    summary["notes"].append("V21.8 is the official entry/exit decision-support baseline: selection remains unchanged, no fixed take-profit, no legacy fixed stop, no new hard stop, T2 required for TCT entry, and HOLD/PROTECT/EXIT requires multifactor deterioration with temporal confirmation. No real orders.")
    (outdir/"SUMMARY.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    return summary
