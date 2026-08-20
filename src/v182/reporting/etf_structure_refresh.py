from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd

from v182.io.frames import apply_observations, is_missing
from v182.sources.etf_structural_data import collect_etf_structural_data
from v182.sources.yfinance_funds import collect_fund_structure

ROOT=Path(__file__).resolve().parents[3]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path,sep=";",encoding="utf-8-sig",low_memory=False)


def _merge_ready_observations(raw:list[dict],ticker_to_isin:dict[str,str],as_of:str)->list[dict]:
    """Attach validated ETF identity and evidence metadata to yfinance observations."""
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


def _governed_structural_observations(raw:list[dict])->list[dict]:
    """Map the collector's exact-ISIN proof onto the existing merge contract.

    The core merge engine already governs ISIN_MATCHED. V21.10 keeps the more
    specific EXACT_ISIN_SOURCE_MATCH marker as provenance detail rather than
    widening ACCEPTED_VALIDATION_STATUSES. Unexpected statuses are left intact
    so the merge engine can continue to fail closed.
    """
    ready=[]
    for obs in raw:
        row=dict(obs)
        if row.get("validation_status") == "EXACT_ISIN_SOURCE_MATCH":
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


def run(root: Path=ROOT) -> dict:
    """Enrich ETF structural data before ETF MT scoring.

    V21.10 adds TER and explicit-EUR fund assets through issuer evidence A and
    exact-ISIN justETF fallback B. The pre-existing yfinance fund-structure
    collector remains evidence C. Every source uses the same provenance merge;
    missing values stay missing and stronger evidence cannot be silently replaced.
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

    # V21.10 structural TER/AUM layer. Unit tests with reduced frames that do not
    # contain a provider column deliberately skip network collection.
    structural_observations=[]
    structural_failures=[]
    structural_metrics={"status":"SKIPPED_PROVIDER_COLUMN_MISSING","requested":0}
    structural_quarantined=[]
    structural_changed=0
    if "provider" in df.columns:
        raw_structural_observations,structural_failures,structural_metrics=collect_etf_structural_data(df)
        structural_observations=_governed_structural_observations(raw_structural_observations)
        before_structural=_snapshot(df,structural_observations)
        df,structural_quarantined=apply_observations(df,structural_observations)
        after_structural=_snapshot(df,structural_observations)
        structural_changed=sum(_changed(before_structural.get(key),after_structural.get(key)) for key in before_structural)

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
    yfinance_changed=sum(_changed(before.get(key),after.get(key)) for key in before)

    df.to_csv(outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",sep=";",index=False,encoding="utf-8-sig")
    audit_dir=outputs/"audit"; audit_dir.mkdir(parents=True,exist_ok=True)
    gaps_dir=outputs/"gaps"; gaps_dir.mkdir(parents=True,exist_ok=True)
    merge_failures=[{**q,"source_stage":"PROVENANCE_MERGE"} for q in structural_quarantined+quarantined]
    failures=structural_failures+collector_failures+merge_failures
    if failures:
        pd.DataFrame(failures).to_csv(gaps_dir/"V21_10_ETF_STRUCTURE_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")

    coverage={field:_coverage(df,field) for field in (
        "ter_pct","fund_total_assets_eur_m","aum_m","diversification_direct_score",
        "direct_sector_hhi","direct_top_holdings_concentration_pct","direct_holdings_count",
    )}
    payload={
        "status":"SUCCESS",
        "version":"V21.10_ETF_STRUCTURAL_DATA",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "source":str(source.relative_to(root)),
        "tickers_requested":len(ticker_to_isin),
        "structural_source":structural_metrics,
        "structural_merge_observations":len(structural_observations),
        "structural_changed_cells":int(structural_changed),
        "structural_merge_quarantined":len(structural_quarantined),
        "yfinance_raw_observations":len(raw_observations),
        "yfinance_merge_observations":len(observations),
        "yfinance_changed_cells":int(yfinance_changed),
        "changed_cells":int(structural_changed+yfinance_changed),
        "collector_failures":len(structural_failures)+len(collector_failures),
        "merge_quarantined":len(structural_quarantined)+len(quarantined),
        "failures":len(failures),
        "coverage_pct":coverage,
        "governance":{
            "mt_dynamic_38_unchanged":True,
            "weights_unchanged":True,
            "missing_structure_not_imputed":True,
            "provenance_merge_enabled":True,
            "issuer_structural_evidence_level":"A",
            "justetf_structural_evidence_level":"B",
            "yfinance_structure_evidence_level":"C",
            "exact_isin_source_match_required":True,
            "exact_isin_detail_preserved_in_provenance":True,
            "merge_validation_status":"ISIN_MATCHED",
            "core_accepted_validation_statuses_unchanged":True,
            "fx_conversion_for_fund_assets":False,
            "quote_currency_used_as_asset_currency":False,
            "stronger_retained_evidence_cannot_be_silently_overwritten":True,
            "diversification_formula":"100*(1-direct_sector_hhi)",
            "top_holdings_concentration_kept_separate":True,
        },
    }
    encoded=json.dumps(payload,ensure_ascii=False,indent=2)
    (audit_dir/"V21_10_ETF_STRUCTURAL_DATA.json").write_text(encoded,encoding="utf-8")
    # Keep the legacy audit path for downstream consumers while publishing the
    # explicit V21.10 path above.
    (audit_dir/"V21_ETF_FUND_STRUCTURE.json").write_text(encoded,encoding="utf-8")
    print(encoded)
    return payload


if __name__=="__main__":
    run()
