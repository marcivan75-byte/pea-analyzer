from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd

from v182.io.frames import apply_observations, is_missing
from v182.features.etf_rank_trajectory import update_etf_rank_trajectories
from v182.sources.yfinance_funds import collect_fund_structure
from v182.sources.boursorama_etf import fetch_boursorama_etf_rankings

ROOT=Path(__file__).resolve().parents[3]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path,sep=";",encoding="utf-8-sig",low_memory=False)


def _merge_ready_observations(raw:list[dict],ticker_to_isin:dict[str,str],as_of:str)->list[dict]:
    """Attach validated ETF identity and evidence metadata to structural observations."""
    ready=[]
    for obs in raw:
        ticker=str(obs.get("ticker") or "").strip()
        isin=ticker_to_isin.get(ticker)
        field=str(obs.get("field") or "").strip()
        if not isin or not field:
            continue
        ready.append({
            "universe":"ETF",
            "isin":str(isin),
            "field":field,
            "value":obs.get("value"),
            "source":str(obs.get("source") or "yfinance.funds_data"),
            "source_url":"",
            "evidence_level":"C",
            "as_of":as_of,
            "validation_status":"AUTO_MATCH",
        })
    return ready


def _snapshot(frame:pd.DataFrame,observations:list[dict])->dict[tuple[str,str],object]:
    indexed=frame.set_index(frame["isin"].astype(str),drop=False)
    out={}
    for obs in observations:
        key=(str(obs["isin"]),str(obs["field"]))
        field=key[1]
        out[key]=indexed.at[key[0],field] if key[0] in indexed.index and field in indexed.columns else pd.NA
    return out


def _changed(before,after)->bool:
    if is_missing(before) and is_missing(after): return False
    if is_missing(before) != is_missing(after): return True
    return str(before).strip()!=str(after).strip()


def run(root: Path=ROOT) -> dict:
    """Enrich ETF structure, source ranks and canonical rank trajectories.

    The full 268-field ETF referential is preserved. Boursorama/Morningstar raw
    annual ranks stay a separate shadow source. Canonical rank_cat_1y/3y/5y are
    snapshotted PIT and may emit 12/24/36m trajectory challengers when enough
    history exists. No rank source or trajectory can modify the reference MT
    decision before dedicated validation.
    """
    outputs=root/"outputs"
    source=outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not source.exists():
        source=root/"inputs"/"V18.2_PEA_ETF_MASTER.csv"
    if not source.exists():
        raise FileNotFoundError("ETF_MASTER_NOT_FOUND")
    df=_read_csv(source)
    if "isin" not in df.columns or "yahoo_ticker" not in df.columns:
        raise RuntimeError("ETF_MASTER_MISSING_ISIN_OR_YAHOO_TICKER")

    valid=df[["isin","yahoo_ticker"]].dropna().copy()
    valid["yahoo_ticker"]=valid["yahoo_ticker"].astype(str).str.strip()
    valid=valid[~valid["yahoo_ticker"].isin({"","nan","None","none","<NA>","N/A","NA","NULL"})]
    ticker_to_isin=dict(zip(valid["yahoo_ticker"],valid["isin"].astype(str)))
    raw_observations,collector_failures=collect_fund_structure(list(ticker_to_isin))
    as_of=datetime.now(timezone.utc).date().isoformat()
    observations=_merge_ready_observations(raw_observations,ticker_to_isin,as_of)
    before=_snapshot(df,observations)
    df,quarantined=apply_observations(df,observations)
    after=_snapshot(df,observations)
    changed_cells=sum(_changed(before.get(key),after.get(key)) for key in before)

    rank_observations,rank_failures=fetch_boursorama_etf_rankings(
        df,
        root/"state"/"boursorama"/"ETF_CATEGORY_RANK_HISTORY.csv",
    )
    rank_before=_snapshot(df,rank_observations)
    df,rank_quarantined=apply_observations(df,rank_observations)
    rank_after=_snapshot(df,rank_observations)
    rank_changed=sum(_changed(rank_before.get(key),rank_after.get(key)) for key in rank_before)

    trajectory_observations,trajectory_failures=update_etf_rank_trajectories(
        df,
        root/"state"/"boursorama"/"ETF_CANONICAL_CATEGORY_RANK_HISTORY.csv",
    )
    trajectory_before=_snapshot(df,trajectory_observations)
    df,trajectory_quarantined=apply_observations(df,trajectory_observations)
    trajectory_after=_snapshot(df,trajectory_observations)
    trajectory_changed=sum(_changed(trajectory_before.get(key),trajectory_after.get(key)) for key in trajectory_before)

    df.to_csv(outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",sep=";",index=False,encoding="utf-8-sig")
    audit_dir=outputs/"audit"; audit_dir.mkdir(parents=True,exist_ok=True)
    gaps_dir=outputs/"gaps"; gaps_dir.mkdir(parents=True,exist_ok=True)
    merge_failures=[{**q,"source_stage":"PROVENANCE_MERGE"} for q in quarantined]
    rank_merge_failures=[{**q,"source_stage":"BOURSORAMA_PROVENANCE_MERGE"} for q in rank_quarantined]
    trajectory_merge_failures=[{**q,"source_stage":"ETF_RANK_TRAJECTORY_PROVENANCE_MERGE"} for q in trajectory_quarantined]
    failures=collector_failures+merge_failures+rank_failures+rank_merge_failures+trajectory_failures+trajectory_merge_failures
    if collector_failures or merge_failures:
        pd.DataFrame(collector_failures+merge_failures).to_csv(gaps_dir/"V21_ETF_FUND_STRUCTURE_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")
    if rank_failures or rank_merge_failures:
        pd.DataFrame(rank_failures+rank_merge_failures).to_csv(gaps_dir/"V21_ETF_BOURSORAMA_RANK_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")
    if trajectory_failures or trajectory_merge_failures:
        pd.DataFrame(trajectory_failures+trajectory_merge_failures).to_csv(gaps_dir/"V21_ETF_RANK_TRAJECTORY_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")

    coverage={}
    for field in ("diversification_direct_score","direct_sector_hhi","direct_top_holdings_concentration_pct","direct_holdings_count"):
        if field in df.columns:
            coverage[field]=round(float((~df[field].apply(is_missing)).mean()*100.0),2)
        else:
            coverage[field]=0.0
    rank_coverage=0.0
    if "boursorama_category_rank_latest" in df.columns:
        rank_coverage=round(float((~df["boursorama_category_rank_latest"].apply(is_missing)).mean()*100.0),2)
    trajectory_coverage={}
    for field in ("rank_cat_trajectory_12m","rank_cat_trajectory_24m","rank_cat_trajectory_36m"):
        trajectory_coverage[field]=round(float((~df[field].apply(is_missing)).mean()*100.0),2) if field in df.columns else 0.0
    payload={
        "status":"SUCCESS",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "source":str(source.relative_to(root)),
        "tickers_requested":len(ticker_to_isin),
        "raw_observations":len(raw_observations),
        "merge_observations":len(observations),
        "changed_cells":int(changed_cells),
        "collector_failures":len(collector_failures),
        "merge_quarantined":len(quarantined),
        "boursorama_rank_observations":len(rank_observations),
        "boursorama_rank_changed_cells":int(rank_changed),
        "boursorama_rank_failures":len(rank_failures),
        "boursorama_rank_merge_quarantined":len(rank_quarantined),
        "boursorama_rank_coverage_pct":rank_coverage,
        "canonical_rank_trajectory_observations":len(trajectory_observations),
        "canonical_rank_trajectory_changed_cells":int(trajectory_changed),
        "canonical_rank_trajectory_failures":len(trajectory_failures),
        "canonical_rank_trajectory_merge_quarantined":len(trajectory_quarantined),
        "canonical_rank_trajectory_coverage_pct":trajectory_coverage,
        "failures":len(failures),
        "coverage_pct":coverage,
        "governance":{
            "etf_referential_criteria_count":268,
            "mt_target_composite_criteria_count":43,
            "mt_dynamic_historical_subblock_count":38,
            "mt_structural_target_count":5,
            "mt_dynamic_38_historical_attribution_unchanged":True,
            "missing_structure_not_imputed":True,
            "provenance_merge_enabled":True,
            "yfinance_structure_evidence_level":"C",
            "stronger_retained_evidence_cannot_be_silently_overwritten":True,
            "diversification_formula":"100*(1-direct_sector_hhi)",
            "top_holdings_concentration_kept_separate":True,
            "boursorama_category_rank_status":"SHADOW_CONFIRMATION",
            "boursorama_category_rank_method":"RAW_MORNINGSTAR_ANNUAL_RANK_AS_PUBLISHED_NO_PERCENTILE_FABRICATION",
            "boursorama_category_rank_decision_influence":0.0,
            "boursorama_rank_history":"state/boursorama/ETF_CATEGORY_RANK_HISTORY.csv",
            "canonical_rank_history":"state/boursorama/ETF_CANONICAL_CATEGORY_RANK_HISTORY.csv",
            "canonical_rank_semantic":"1_BEST_100_WORST",
            "trajectory_positive_semantic":"IMPROVEMENT_PRIOR_MINUS_CURRENT",
            "trajectory_first_snapshot_policy":"MISSING_NO_IMPUTATION",
            "boursorama_raw_rank_not_substituted_for_canonical_rank_without_semantic_proof":True,
            "rank_trajectory_decision_influence":0.0,
            "boursorama_missing_rank_not_imputed":True
        },
    }
    (audit_dir/"V21_ETF_FUND_STRUCTURE.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    return payload


if __name__=="__main__":
    run()
