from __future__ import annotations

from pathlib import Path
import pandas as pd

from v182.decision.committee_master import classify_sector, sector_ranking


def apply(root: Path, summary: dict) -> dict:
    ranking_path=root/"outputs"/"etf_mt_v2081"/"V20.8.2_ETF_MT_DYNAMIC_RANKING.csv"
    decisions_path=root/"outputs"/"committee_master"/"COMMITTEE_DECISIONS.csv"
    etf_path=root/"outputs"/"V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not ranking_path.exists() or not decisions_path.exists():
        summary["etf_mt_v2082"]={"status":"BLOCKED_DYNAMIC_OUTPUT_MISSING"}; return summary
    ranking=pd.read_csv(ranking_path,sep=";",encoding="utf-8-sig",low_memory=False); decisions=pd.read_csv(decisions_path,sep=";",encoding="utf-8-sig",low_memory=False); etfs=pd.read_csv(etf_path,sep=";",encoding="utf-8-sig",low_memory=False) if etf_path.exists() else pd.DataFrame()
    master=etfs.set_index("isin",drop=False) if not etfs.empty and "isin" in etfs.columns else pd.DataFrame(); rows=[]
    for _,rr in ranking.iterrows():
        isin=str(rr.get("instrument_id",rr.get("isin","")) or ""); m=master.loc[isin] if not master.empty and isin in master.index else rr
        if isinstance(m,pd.DataFrame): m=m.iloc[0]
        score=pd.to_numeric(pd.Series([rr.get("dynamic_score_final")]),errors="coerce").iloc[0]; coverage=pd.to_numeric(pd.Series([rr.get("dynamic_weight_coverage_pct")]),errors="coerce").iloc[0]; available=pd.to_numeric(pd.Series([rr.get("dynamic_available_criteria")]),errors="coerce").iloc[0]; decision=str(rr.get("dynamic_decision","BLOCK_DATA")); status="SCORABLE" if pd.notna(score) and pd.notna(coverage) and coverage>=70 else "BLOCK_DATA"
        rows.append({"asset_class":"ETF","horizon":"MT","isin":isin,"name":str(m.get("name",rr.get("name","")) or ""),"sector":classify_sector(m,"ETF"),"score":score,"coverage_pct":coverage if pd.notna(coverage) else 0.0,"status":status,"decision":decision,"active_criteria":38,"available_criteria":int(available) if pd.notna(available) else 0,"score_source":"V20.8.2_DYNAMIC_AVAILABLE_38","backtest_attribution":"NONE_FOR_V20.8.2_UNTIL_DEDICATED_PIT_BACKTEST","notes":"38 PIT criteria; available criteria dynamically renormalized to 100%. V20.8.1 exact-complete core retained separately for historical 90.91% attribution only.","dynamic_missing_policy":"AVAILABLE_CRITERIA_RENORMALIZED_TO_100_PERCENT","selected":bool(rr.get("dynamic_selected",False))})
    decisions=decisions[~((decisions["asset_class"].astype(str)=="ETF")&(decisions["horizon"].astype(str)=="MT"))].copy(); dynamic=pd.DataFrame(rows)
    if "generated_at_utc" in decisions.columns: dynamic["generated_at_utc"]=summary.get("generated_at_utc")
    if "live_orders_enabled" in decisions.columns: dynamic["live_orders_enabled"]=False
    decisions=pd.concat([decisions,dynamic],ignore_index=True,sort=False); decisions.to_csv(decisions_path,sep=";",index=False,encoding="utf-8-sig"); sector_ranking(decisions).to_csv(root/"outputs"/"committee_master"/"SECTOR_RANKING.csv",sep=";",index=False,encoding="utf-8-sig")
    summary["status_counts"]=decisions.groupby(["asset_class","horizon","status"],dropna=False).size().reset_index(name="count").to_dict("records"); summary["decision_counts"]=decisions.groupby(["asset_class","horizon","decision"],dropna=False).size().reset_index(name="count").to_dict("records")
    summary["etf_mt_v2082"]={"status":"SHADOW_ACTIVE_CHALLENGER","rows":int(len(dynamic)),"scorable":int((dynamic["status"]=="SCORABLE").sum()) if not dynamic.empty else 0,"buy_candidates":int((dynamic["decision"]=="BUY_CANDIDATE").sum()) if not dynamic.empty else 0,"minimum_weighted_coverage_pct":70,"missing_policy":"AVAILABLE_CRITERIA_RENORMALIZED_TO_100_PERCENT","historical_performance_attribution":"NONE","v2081_reference":"90.91% OOS remains exact complete-38 V20.8.1 only"}
    summary.setdefault("outputs",{})["etf_mt_dynamic_ranking"]="outputs/etf_mt_v2081/V20.8.2_ETF_MT_DYNAMIC_RANKING.csv"
    return summary
