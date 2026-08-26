from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import pandas as pd

from v182.io.frames import apply_observations, is_missing
from v182.sources.etf_inception_data import collect_etf_inception_data
from v182.sources.etf_structural_data import collect_etf_structural_data
from v182.sources.yfinance_funds import collect_fund_structure
from v182.state.etf_structure_state import (
    load_replay_observations,
    load_state_config,
    write_structural_state_snapshot,
)

ROOT=Path(__file__).resolve().parents[3]
STRUCTURAL_SOURCE_FIELDS={"ter_pct","fund_total_assets_eur_m","aum_m","official_benchmark"}
INCEPTION_SOURCE_FIELDS={"share_class_inception_date","listing_or_launch_date","reported_first_nav_date"}
FUND_STRUCTURE_FIELDS={
    "diversification_direct_score","direct_diversification_score","direct_sector_hhi",
    "direct_top_holdings_concentration_pct","top_holdings_concentration_pct","top_holdings_observed_count",
}


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path,sep=";",encoding="utf-8-sig",low_memory=False)


def _merge_ready_observations(raw:list[dict],ticker_to_isin:dict[str,str],as_of:str)->list[dict]:
    ready=[]
    for obs in raw:
        ticker=str(obs.get("ticker") or "").strip()
        isin=ticker_to_isin.get(ticker)
        field=str(obs.get("field") or "").strip()
        if not isin or not field:
            continue
        ready.append({
            "universe":"ETF","isin":str(isin),"field":field,"value":obs.get("value"),
            "source":str(obs.get("source") or "yfinance.funds_data"),"source_url":"",
            "evidence_level":"C","as_of":as_of,"validation_status":"AUTO_MATCH",
        })
    return ready


def _governed_structural_observations(raw:list[dict],now=None)->list[dict]:
    reference=pd.Timestamp(now or datetime.now(timezone.utc))
    if reference.tzinfo is None:
        reference=reference.tz_localize("UTC")
    else:
        reference=reference.tz_convert("UTC")
    ready=[]
    for obs in raw:
        row=dict(obs)
        observed_at=pd.to_datetime(row.get("as_of"),errors="coerce",utc=True)
        collected_at=pd.to_datetime(row.get("collected_at"),errors="coerce",utc=True)
        ceiling=reference
        if pd.notna(collected_at) and collected_at < ceiling:
            ceiling=collected_at
        if pd.isna(observed_at):
            row["temporal_validation_detail"]="STRUCTURAL_AS_OF_UNPARSEABLE"
            row["validation_status"]="TEMPORAL_REJECTED_AS_OF_UNPARSEABLE"
        elif observed_at > ceiling + pd.Timedelta(days=1):
            row["temporal_validation_detail"]="STRUCTURAL_FUTURE_AS_OF_REJECTED"
            row["validation_status"]="TEMPORAL_REJECTED_FUTURE_AS_OF"
        elif row.get("validation_status") == "EXACT_ISIN_SOURCE_MATCH":
            row["identity_validation_detail"]="EXACT_ISIN_SOURCE_MATCH"
            row["validation_status"]="ISIN_MATCHED"
        ready.append(row)
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


def _coverage(frame:pd.DataFrame,field:str)->float:
    if field not in frame.columns:
        return 0.0
    return round(float((~frame[field].apply(is_missing)).mean()*100.0),2)


def _obs_timestamp(obs:dict) -> pd.Timestamp | None:
    for field in ("collected_at","as_of"):
        ts=pd.to_datetime(obs.get(field),errors="coerce",utc=True)
        if pd.notna(ts):
            return ts
    return None


def _fresh_source_isins(replay:list[dict], fields:set[str], max_age_days:int, now=None)->set[str]:
    """Identify instruments recently refreshed for one physical source family.

    Replay observations are already governed field-by-field by ETF_STRUCTURE_STATE.
    This additional source-family age prevents the Weekly from re-calling the same
    provider every week merely because the state was replayed successfully.
    """
    reference=pd.Timestamp(now or datetime.now(timezone.utc))
    if reference.tzinfo is None:
        reference=reference.tz_localize("UTC")
    else:
        reference=reference.tz_convert("UTC")
    latest:dict[str,pd.Timestamp]={}
    for obs in replay:
        if str(obs.get("field") or "") not in fields:
            continue
        isin=str(obs.get("isin") or "").strip()
        if not isin:
            continue
        ts=_obs_timestamp(obs)
        if ts is None:
            continue
        if isin not in latest or ts > latest[isin]:
            latest[isin]=ts
    cutoff=reference-pd.Timedelta(days=int(max_age_days))
    return {isin for isin,ts in latest.items() if ts >= cutoff}


def _due_frame(frame:pd.DataFrame, fresh_isins:set[str]) -> pd.DataFrame:
    if "isin" not in frame.columns:
        return frame.copy()
    keys=frame["isin"].astype(str).str.strip()
    return frame[~keys.isin(fresh_isins)].copy()


def run(root: Path=ROOT) -> dict:
    """Enrich ETF structure using existing provenance state as the persistent cache.

    V22.2 keeps the original collectors and merge rules but only calls them for
    instruments whose source-family freshness has expired. No new cache family,
    scoring rule, weight, threshold or imputation policy is introduced.
    """
    outputs=root/"outputs"
    source=outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not source.exists(): source=root/"inputs"/"V18.2_PEA_ETF_MASTER.csv"
    if not source.exists(): raise FileNotFoundError("ETF_MASTER_NOT_FOUND")
    df=_read_csv(source)
    if "isin" not in df.columns or "yahoo_ticker" not in df.columns:
        raise RuntimeError("ETF_MASTER_MISSING_ISIN_OR_YAHOO_TICKER")

    state_cfg_path=root/"config"/"ETF_STRUCTURE_STATE_V21_15.json"
    if not state_cfg_path.exists(): state_cfg_path=ROOT/"config"/"ETF_STRUCTURE_STATE_V21_15.json"
    state_cfg=load_state_config(state_cfg_path)
    state_replay_obs,state_replay_diag=load_replay_observations(state_cfg,root=root)
    state_replay_quarantined=[]
    if state_replay_obs:
        df,state_replay_quarantined=apply_observations(df,state_replay_obs)

    structural_fresh=_fresh_source_isins(state_replay_obs,STRUCTURAL_SOURCE_FIELDS,62)
    inception_fresh=_fresh_source_isins(state_replay_obs,INCEPTION_SOURCE_FIELDS,365)
    fund_structure_fresh=_fresh_source_isins(state_replay_obs,FUND_STRUCTURE_FIELDS,31)
    all_isins={str(x).strip() for x in df["isin"].dropna().astype(str) if str(x).strip()}

    structural_observations=[]; structural_failures=[]
    structural_metrics={"status":"SKIPPED_PROVIDER_COLUMN_MISSING","requested":0}
    structural_quarantined=[]; structural_changed=0
    inception_observations=[]; inception_failures=[]
    inception_metrics={"status":"SKIPPED_PROVIDER_COLUMN_MISSING","requested":0}
    inception_quarantined=[]; inception_changed=0
    if "provider" in df.columns:
        structural_due=_due_frame(df,structural_fresh)
        if structural_due.empty:
            structural_metrics={"status":"CACHE_REPLAY_FRESH","requested":0,"cache_reused_isins":len(structural_fresh)}
        else:
            raw_structural_observations,structural_failures,structural_metrics=collect_etf_structural_data(structural_due)
            structural_metrics={**dict(structural_metrics or {}),"cache_reused_isins":len(all_isins & structural_fresh),"network_due_isins":int(len(structural_due))}
            structural_observations=_governed_structural_observations(raw_structural_observations)
            before_structural=_snapshot(df,structural_observations)
            df,structural_quarantined=apply_observations(df,structural_observations)
            after_structural=_snapshot(df,structural_observations)
            structural_changed=sum(_changed(before_structural.get(key),after_structural.get(key)) for key in before_structural)

        inception_due=_due_frame(df,inception_fresh)
        if inception_due.empty:
            inception_metrics={"status":"CACHE_REPLAY_FRESH","requested":0,"cache_reused_isins":len(inception_fresh)}
        else:
            raw_inception_observations,inception_failures,inception_metrics=collect_etf_inception_data(inception_due)
            inception_metrics={**dict(inception_metrics or {}),"cache_reused_isins":len(all_isins & inception_fresh),"network_due_isins":int(len(inception_due))}
            inception_observations=_governed_structural_observations(raw_inception_observations)
            before_inception=_snapshot(df,inception_observations)
            df,inception_quarantined=apply_observations(df,inception_observations)
            after_inception=_snapshot(df,inception_observations)
            inception_changed=sum(_changed(before_inception.get(key),after_inception.get(key)) for key in before_inception)

    valid=df[["isin","yahoo_ticker"]].dropna().copy()
    valid["yahoo_ticker"]=valid["yahoo_ticker"].astype(str).str.strip()
    valid=valid[~valid["yahoo_ticker"].isin({"","nan","None","none","<NA>","N/A","NA","NULL"})]
    valid=valid[~valid["isin"].astype(str).isin(fund_structure_fresh)]
    ticker_to_isin=dict(zip(valid["yahoo_ticker"],valid["isin"].astype(str)))
    if ticker_to_isin:
        raw_observations,collector_failures=collect_fund_structure(list(ticker_to_isin))
    else:
        raw_observations,collector_failures=[],[]
    as_of=datetime.now(timezone.utc).date().isoformat()
    observations=_merge_ready_observations(raw_observations,ticker_to_isin,as_of)
    before=_snapshot(df,observations)
    df,quarantined=apply_observations(df,observations)
    after=_snapshot(df,observations)
    yfinance_changed=sum(_changed(before.get(key),after.get(key)) for key in before)

    df.to_csv(outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",sep=";",index=False,encoding="utf-8-sig")
    state_write=write_structural_state_snapshot(df,state_cfg,root=root)
    audit_dir=outputs/"audit"; audit_dir.mkdir(parents=True,exist_ok=True)
    state_write_path=root/str(state_cfg["audit_write_path"])
    state_write_path.parent.mkdir(parents=True,exist_ok=True)
    state_write_path.write_text(json.dumps(state_write,ensure_ascii=False,indent=2),encoding="utf-8")

    gaps_dir=outputs/"gaps"; gaps_dir.mkdir(parents=True,exist_ok=True)
    merge_failures=[{**q,"source_stage":"PROVENANCE_MERGE"} for q in state_replay_quarantined+structural_quarantined+inception_quarantined+quarantined]
    failures=structural_failures+inception_failures+collector_failures+merge_failures
    if failures:
        pd.DataFrame(failures).to_csv(gaps_dir/"V21_10_ETF_STRUCTURE_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")

    coverage={field:_coverage(df,field) for field in (
        "ter_pct","fund_total_assets_eur_m","aum_m","diversification_direct_score",
        "direct_sector_hhi","direct_top_holdings_concentration_pct","direct_holdings_count",
        "share_class_inception_date","listing_or_launch_date","reported_first_nav_date",
        "official_benchmark","tracking_error_1y_pct","tracking_error_3y_pct","tracking_error_5y_pct",
    )}
    payload={
        "status":"SUCCESS","version":"V22.2_ETF_STRUCTURAL_DELTA_ONLY",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "github_run_id":str(os.environ.get("GITHUB_RUN_ID") or ""),
        "github_run_attempt":str(os.environ.get("GITHUB_RUN_ATTEMPT") or ""),
        "source":str(source.relative_to(root)),
        "tickers_requested":len(ticker_to_isin),
        "state_replay":state_replay_diag,
        "state_replay_merge_quarantined":len(state_replay_quarantined),
        "delta_cache":{
            "total_isins":len(all_isins),
            "structural_fresh_isins":len(all_isins & structural_fresh),
            "structural_due_isins":len(all_isins-structural_fresh),
            "inception_fresh_isins":len(all_isins & inception_fresh),
            "inception_due_isins":len(all_isins-inception_fresh),
            "fund_structure_fresh_isins":len(all_isins & fund_structure_fresh),
            "fund_structure_due_isins":len(all_isins-fund_structure_fresh),
            "source_family_ttl_days":{"issuer_structure":62,"inception":365,"fund_structure":31},
        },
        "structural_source":structural_metrics,
        "structural_merge_observations":len(structural_observations),
        "structural_changed_cells":int(structural_changed),
        "structural_merge_quarantined":len(structural_quarantined),
        "inception_source":inception_metrics,
        "inception_merge_observations":len(inception_observations),
        "inception_changed_cells":int(inception_changed),
        "inception_merge_quarantined":len(inception_quarantined),
        "yfinance_raw_observations":len(raw_observations),
        "yfinance_merge_observations":len(observations),
        "yfinance_changed_cells":int(yfinance_changed),
        "changed_cells":int(structural_changed+inception_changed+yfinance_changed),
        "collector_failures":len(structural_failures)+len(inception_failures)+len(collector_failures),
        "merge_quarantined":len(state_replay_quarantined)+len(structural_quarantined)+len(inception_quarantined)+len(quarantined),
        "failures":len(failures),"coverage_pct":coverage,"state_write":state_write,
        "governance":{
            "existing_state_provenance_cache_reused":True,
            "new_cache_family_created":False,
            "network_refresh_delta_only":True,
            "mt_dynamic_38_unchanged":True,"weights_unchanged":True,"thresholds_unchanged":True,
            "missing_structure_not_imputed":True,"future_structural_as_of_rejected_before_master":True,
            "daily_structural_network_scrape":False,"provenance_merge_enabled":True,
            "issuer_structural_evidence_level":"A","issuer_share_class_inception_evidence_level":"A",
            "justetf_structural_evidence_level":"B","justetf_listing_launch_evidence_level":"B",
            "reported_first_nav_context_only":True,"exact_isin_source_match_required":True,
            "exact_isin_detail_preserved_in_provenance":True,"merge_validation_status":"ISIN_MATCHED",
            "core_accepted_validation_statuses_unchanged":True,"fx_conversion_for_fund_assets":False,
            "quote_currency_used_as_asset_currency":False,"stronger_retained_evidence_cannot_be_silently_overwritten":True,
            "diversification_formula":"100*(1-direct_sector_hhi)","top_holdings_concentration_kept_separate":True,
            "inception_evidence_changes_calibration_eligibility":False,"synthetic_pre_inception_history":False,
            "official_benchmark_requires_explicit_label":True,"benchmark_name_inference":False,
            "benchmark_price_symbol_inference":False,"tracking_error_activation":False,"stress_calibration_weight":0.0,
        },
    }
    encoded=json.dumps(payload,ensure_ascii=False,indent=2)
    (audit_dir/"V21_10_ETF_STRUCTURAL_DATA.json").write_text(encoded,encoding="utf-8")
    (audit_dir/"V21_ETF_FUND_STRUCTURE.json").write_text(encoded,encoding="utf-8")
    (audit_dir/"V21_16_ETF_INCEPTION_EVIDENCE.json").write_text(encoded,encoding="utf-8")
    (audit_dir/"V21_18_ETF_BENCHMARK_COVERAGE.json").write_text(encoded,encoding="utf-8")
    print(encoded)
    return payload


if __name__=="__main__":
    run()
