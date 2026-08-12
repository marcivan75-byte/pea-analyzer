from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd

from v182.io.frames import apply_observations, is_missing
from v182.sources.yfinance_funds import collect_fund_structure

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
    """Enrich ETF structure through the same evidence/provenance merge as other observations.

    Runs after the main refresh and before Committee scoring. yfinance structural
    fields are evidence C: they may fill missing cells but cannot silently replace
    stronger retained evidence. Missing data remain missing and all merge attempts
    enter the append-only provenance ledger.
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

    df.to_csv(outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",sep=";",index=False,encoding="utf-8-sig")
    audit_dir=outputs/"audit"; audit_dir.mkdir(parents=True,exist_ok=True)
    gaps_dir=outputs/"gaps"; gaps_dir.mkdir(parents=True,exist_ok=True)
    merge_failures=[{**q,"source_stage":"PROVENANCE_MERGE"} for q in quarantined]
    failures=collector_failures+merge_failures
    if failures:
        pd.DataFrame(failures).to_csv(gaps_dir/"V21_ETF_FUND_STRUCTURE_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")

    coverage={}
    for field in ("diversification_direct_score","direct_sector_hhi","direct_top_holdings_concentration_pct","direct_holdings_count"):
        if field in df.columns:
            coverage[field]=round(float((~df[field].apply(is_missing)).mean()*100.0),2)
        else:
            coverage[field]=0.0
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
        "failures":len(failures),
        "coverage_pct":coverage,
        "governance":{
            "mt_dynamic_38_unchanged":True,
            "missing_structure_not_imputed":True,
            "provenance_merge_enabled":True,
            "yfinance_structure_evidence_level":"C",
            "stronger_retained_evidence_cannot_be_silently_overwritten":True,
            "diversification_formula":"100*(1-direct_sector_hhi)",
            "top_holdings_concentration_kept_separate":True,
        },
    }
    (audit_dir/"V21_ETF_FUND_STRUCTURE.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    return payload


if __name__=="__main__":
    run()
