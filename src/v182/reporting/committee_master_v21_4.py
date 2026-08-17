from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

from v182.decision.committee_master import load_registry, decisions_from_scores, sector_ranking
from v182.io.frames import is_missing
from v182.reporting import committee_master_gold_v1_1
from v182.reporting.sector_rotation_v2_committee_bridge import build_committee_sector_rotation_v2_status
from v182.sources.postselection_market_sheets import enrich_postselection

ROOT=Path(__file__).resolve().parents[3]
HORIZONS=["CT","MT","LT","SHORT","TOP_DOWN"]
CDC_FIELDS=[
    "next_earnings_date_fh","next_earnings_hour_fh","earnings_days_to_event_fh",
    "eps_estimate_next_fh","eps_actual_fh","revenue_estimate_next_fh","revenue_actual_fh",
    "eps_estimate_revision_abs_fh","eps_estimate_revision_pct_fh",
    "amf_public_short_disclosed_sum_pct","amf_public_short_holder_count",
    "amf_public_short_latest_position_date","amf_public_short_latest_publication_date",
    "amf_public_short_days_since_latest_publication","amf_public_short_oldest_retained_publication_date",
    "amf_public_short_max_days_since_retained_publication","amf_public_short_open_publication_count",
    "amf_public_short_proxy_flag","amf_public_short_not_true_current_interest_flag",
]


def _read(path:Path)->pd.DataFrame:
    return pd.read_csv(path,sep=";",encoding="utf-8-sig",low_memory=False)


def _key(frame:pd.DataFrame)->pd.Series:
    return frame["horizon"].astype(str)+"|"+frame["isin"].astype(str)


def _postselection_isins(decisions: pd.DataFrame) -> set[str]:
    mask=(
        (decisions["asset_class"].astype(str)=="ACTION")
        & decisions["horizon"].astype(str).isin(["CT","MT","LT"])
        & decisions["decision"].astype(str).isin(["BUY_CANDIDATE","WATCH"])
    )
    return set(decisions.loc[mask,"isin"].astype(str))


def _attach_cdc_context(decisions: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    available=[field for field in CDC_FIELDS if field in actions.columns]
    if not available:
        out=decisions.copy()
        action_mask=out["asset_class"].astype(str)=="ACTION"
        out["cdc_decision_influence"]=0.0
        out["cdc_data_status"]="NOT_APPLICABLE"
        out.loc[action_mask,"cdc_data_status"]="MISSING"
        return out
    context=actions[["isin",*available]].drop_duplicates("isin").copy()
    out=decisions.merge(context,on="isin",how="left",validate="many_to_one",sort=False)
    action_mask=out["asset_class"].astype(str)=="ACTION"
    present=out.loc[action_mask,available].apply(lambda col: ~col.map(is_missing))
    observed=present.any(axis=1)
    out["cdc_decision_influence"]=0.0
    out["cdc_data_status"]="NOT_APPLICABLE"
    out.loc[action_mask,"cdc_data_status"]="MISSING"
    out.loc[observed.index[observed],"cdc_data_status"]="AVAILABLE"
    return out


def run(root:Path=ROOT)->dict:
    """Reference V21.0 decisions plus V21.7 study-hardened Action challenger.

    V21.7 keeps the criterion-study hardening, CDC/post-selection context and
    main's Sector Rotation V2 diagnostic bridge. Final Action decisions remain
    frozen on V21.0 until dedicated PIT/OOS promotion. Sector Rotation V2 and
    all added context layers remain zero-influence diagnostics.
    """
    summary=committee_master_gold_v1_1.run(root)
    outdir=root/"outputs"/"committee_master"; decisions_path=outdir/"COMMITTEE_DECISIONS.csv"
    actions_path=root/"outputs"/"V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    if not decisions_path.exists() or not actions_path.exists():
        summary["action_dual_track"]={"status":"BLOCKED_INPUT"}
        summary["sector_rotation_v2"]=build_committee_sector_rotation_v2_status(root)
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
        decisions.at[idx,"notes"]=(note+" | FINAL ACTION DECISION uses frozen V21.0 reference weights; V21.7 study-hardened score is challenger-only pending PIT/OOS validation.").strip(" |")
        rows.append({"key":key,"isin":row.get("isin"),"name":row.get("name"),"sector":row.get("sector"),"horizon":row.get("horizon"),"reference_score":ref.get("score"),"reference_coverage_pct":ref.get("coverage_pct"),"reference_decision":ref.get("decision"),"challenger_score":challenger_score,"challenger_coverage_pct":challenger_cov,"challenger_decision":row.get("decision"),"challenger_52w_score":row.get("action_52w_challenger_score"),"challenger_52w_decision":row.get("action_52w_challenger_decision"),"delta_score":(challenger_score-float(ref.get("score"))) if pd.notna(challenger_score) and pd.notna(ref.get("score")) else None})

    comparison=pd.DataFrame(rows)
    comparison.to_csv(outdir/"ACTION_REFERENCE_VS_CHALLENGER_V21_7.csv",sep=";",index=False,encoding="utf-8-sig")
    comparison.to_csv(outdir/"ACTION_REFERENCE_VS_CHALLENGER_V21_4.csv",sep=";",index=False,encoding="utf-8-sig")

    score_guard=decisions["score"].copy(); decision_guard=decisions["decision"].copy()
    decisions=_attach_cdc_context(decisions,actions)
    if not score_guard.reset_index(drop=True).equals(decisions["score"].reset_index(drop=True)):
        raise RuntimeError("CDC_CONTEXT_SCORE_MUTATION_FORBIDDEN")
    if not decision_guard.reset_index(drop=True).equals(decisions["decision"].reset_index(drop=True)):
        raise RuntimeError("CDC_CONTEXT_DECISION_MUTATION_FORBIDDEN")

    shortlist=_postselection_isins(decisions)
    postselection,postselection_failures=enrich_postselection(actions,shortlist)
    postselection.to_csv(outdir/"POSTSELECTION_MARKET_SHEETS.csv",sep=";",index=False,encoding="utf-8-sig")
    gaps=root/"outputs"/"gaps"; gaps.mkdir(parents=True,exist_ok=True)
    if not postselection_failures.empty:
        postselection_failures.to_csv(gaps/"V21_6_3_POSTSELECTION_MARKET_SHEETS_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")
    score_guard=decisions["score"].copy(); decision_guard=decisions["decision"].copy()
    if not postselection.empty:
        decisions=decisions.merge(postselection,on="isin",how="left",validate="many_to_one",sort=False)
    if not score_guard.reset_index(drop=True).equals(decisions["score"].reset_index(drop=True)):
        raise RuntimeError("POSTSELECTION_SCORE_MUTATION_FORBIDDEN")
    if not decision_guard.reset_index(drop=True).equals(decisions["decision"].reset_index(drop=True)):
        raise RuntimeError("POSTSELECTION_DECISION_MUTATION_FORBIDDEN")

    decisions.to_csv(decisions_path,sep=";",index=False,encoding="utf-8-sig")
    sector_ranking(decisions).to_csv(outdir/"SECTOR_RANKING.csv",sep=";",index=False,encoding="utf-8-sig")
    challenger_view=decisions.copy(); mask=(challenger_view["asset_class"].astype(str)=="ACTION") & challenger_view["horizon"].astype(str).isin(HORIZONS)
    challenger_view.loc[mask,"score"]=challenger_view.loc[mask,"action_challenger_score"]
    challenger_view.loc[mask,"coverage_pct"]=challenger_view.loc[mask,"action_challenger_coverage_pct"]
    challenger_view.loc[mask,"status"]=challenger_view.loc[mask,"action_challenger_status"]
    challenger_view.loc[mask,"decision"]=challenger_view.loc[mask,"action_challenger_decision"]
    challenger_sector=sector_ranking(challenger_view)
    challenger_sector.to_csv(outdir/"SECTOR_RANKING_CHALLENGER_V21_7.csv",sep=";",index=False,encoding="utf-8-sig")
    challenger_sector.to_csv(outdir/"SECTOR_RANKING_CHALLENGER_V21_4.csv",sep=";",index=False,encoding="utf-8-sig")

    divergences=int((comparison["reference_decision"].astype(str)!=comparison["challenger_decision"].astype(str)).sum()) if not comparison.empty else 0
    ref_buy=int((comparison["reference_decision"].astype(str)=="BUY_CANDIDATE").sum()) if not comparison.empty else 0
    chal_buy=int((comparison["challenger_decision"].astype(str)=="BUY_CANDIDATE").sum()) if not comparison.empty else 0
    cdc_available=0
    if "cdc_data_status" in decisions.columns:
        cdc_available=int(decisions.loc[decisions["asset_class"].astype(str)=="ACTION","cdc_data_status"].eq("AVAILABLE").sum())
    summary["action_dual_track"]={"status":"ACTIVE_REFERENCE_PLUS_STUDY_HARDENED_SHADOW_CHALLENGER","reference_version":reference_reg.get("version"),"challenger_version":challenger_reg.get("version"),"final_decision_source":"REFERENCE","comparison_rows":int(len(comparison)),"decision_divergences":divergences,"reference_buy_count":ref_buy,"challenger_buy_count":chal_buy,"family_budget_policy":challenger_reg.get("governance",{}).get("family_budget_policy"),"performance_attribution":"NONE_TO_V21_7_CHALLENGER_UNTIL_DEDICATED_PIT_OOS_BACKTEST"}
    summary["cdc_committee_context"]={"status":"ACTIVE_OBSERVED_CONTEXT","available_action_decision_rows":cdc_available,"fields":[field for field in CDC_FIELDS if field in actions.columns],"decision_influence":0.0,"score_mutation_forbidden":True,"decision_mutation_forbidden":True,"amf_short_semantics":"OPEN_PUBLIC_DISCLOSURE_PROXY_NOT_TRUE_CURRENT_SHORT_INTEREST","amf_observation_as_of":"LATEST_RETAINED_PUBLIC_DISCLOSURE_DATE","amf_staleness_fields":["amf_public_short_days_since_latest_publication","amf_public_short_max_days_since_retained_publication"]}
    summary["postselection_market_sheets"]={"status":"ACTIVE_SHADOW_CONFIRMATION","shortlisted_isins":len(shortlist),"enriched_isins":int(len(postselection)),"source_failures":int(len(postselection_failures)),"decision_influence":0.0,"investing_timeframes":["WEEKLY","MONTHLY"],"investing_listing_resolution":"EXPLICIT_URL_OR_UNIQUE_IDENTITY_MATCH_NO_ARBITRARY_ADR_VENUE","signals":["STRONG_BUY","BUY","NEUTRAL","SELL","STRONG_SELL"],"positive_confirmation_can_create_buy":False}
    summary["sector_rotation_v2"]=build_committee_sector_rotation_v2_status(root)
    summary["status_counts"]=decisions.groupby(["asset_class","horizon","status"],dropna=False).size().reset_index(name="count").to_dict("records")
    summary["decision_counts"]=decisions.groupby(["asset_class","horizon","decision"],dropna=False).size().reset_index(name="count").to_dict("records")
    summary.setdefault("outputs",{})["action_reference_vs_challenger"]="outputs/committee_master/ACTION_REFERENCE_VS_CHALLENGER_V21_7.csv"
    summary["outputs"]["action_reference_vs_challenger_legacy_alias"]="outputs/committee_master/ACTION_REFERENCE_VS_CHALLENGER_V21_4.csv"
    summary["outputs"]["sector_ranking_challenger"]="outputs/committee_master/SECTOR_RANKING_CHALLENGER_V21_7.csv"
    summary["outputs"]["sector_ranking_challenger_legacy_alias"]="outputs/committee_master/SECTOR_RANKING_CHALLENGER_V21_4.csv"
    summary["outputs"]["postselection_market_sheets"]="outputs/committee_master/POSTSELECTION_MARKET_SHEETS.csv"
    summary["outputs"]["sector_rotation_v2_shadow"]="outputs/sector_rotation/V2_SECTOR_ROTATION_SHADOW.csv"
    summary["outputs"]["sector_rotation_v2_pit_oos_status"]="outputs/audit/V2_SECTOR_ROTATION_PIT_OOS_STATUS.json"
    summary["outputs"]["sector_rotation_v2_pit_oos_observations"]="outputs/sector_rotation/V2_PIT_OOS_OBSERVATIONS.csv"
    summary["outputs"]["sector_rotation_v2_pit_oos_metrics"]="outputs/sector_rotation/V2_PIT_OOS_SNAPSHOT_METRICS.csv"
    summary.setdefault("notes",[]).append("Actions use frozen V21.0 reference weights for final decisions. V21.7 is a study-hardened challenger: derived threshold scores are folded into canonical criteria, family budgets are explicit, and unvalidated overlays are zero-weight.")
    summary["notes"].append("Finnhub earnings/EPS revisions and AMF open public-short-disclosure proxy fields are copied into Committee Action rows with zero score/decision influence.")
    summary["notes"].append("Boursorama/Investing Action enrichment runs only after BUY/WATCH preselection and remains zero-influence until PIT/OOS validation.")
    summary["notes"].append("Sector Rotation V2 remains Committee diagnostics only with zero influence on Action/ETF scores, decisions, sales or orders until governed PIT/OOS promotion.")
    (outdir/"SUMMARY.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    return summary
