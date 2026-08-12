from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd

from v182.sources.yfinance_funds import collect_fund_structure

ROOT=Path(__file__).resolve().parents[3]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path,sep=";",encoding="utf-8-sig",low_memory=False)


def run(root: Path=ROOT) -> dict:
    """Enrich the current ETF master with observed holdings/sector structure.

    Runs after the main refresh and before Committee scoring. It does not modify
    ETF MT dynamic features or selection. Missing fund-structure data remains
    missing and is explicitly reported.
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
    valid=valid[~valid["yahoo_ticker"].isin({"","nan","None","none"})]
    ticker_to_isin=dict(zip(valid["yahoo_ticker"],valid["isin"]))
    observations,failures=collect_fund_structure(list(ticker_to_isin))

    applied=0
    for obs in observations:
        isin=ticker_to_isin.get(obs.get("ticker"))
        field=obs.get("field")
        value=obs.get("value")
        if isin is None or not field:
            continue
        if field not in df.columns:
            df[field]=pd.NA
        mask=df["isin"].astype(str)==str(isin)
        if mask.any():
            df.loc[mask,field]=value
            applied += int(mask.sum())

    df.to_csv(outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",sep=";",index=False,encoding="utf-8-sig")
    audit_dir=outputs/"audit"; audit_dir.mkdir(parents=True,exist_ok=True)
    gaps_dir=outputs/"gaps"; gaps_dir.mkdir(parents=True,exist_ok=True)
    if failures:
        pd.DataFrame(failures).to_csv(gaps_dir/"V21_ETF_FUND_STRUCTURE_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")

    coverage={}
    for field in ("diversification_direct_score","direct_sector_hhi","direct_top_holdings_concentration_pct","direct_holdings_count"):
        if field in df.columns:
            coverage[field]=round(float(df[field].notna().mean()*100.0),2)
        else:
            coverage[field]=0.0
    payload={
        "status":"SUCCESS",
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "source":str(source.relative_to(root)),
        "tickers_requested":len(ticker_to_isin),
        "observations":len(observations),
        "applied_cells":applied,
        "failures":len(failures),
        "coverage_pct":coverage,
        "governance":{
            "mt_dynamic_38_unchanged":True,
            "missing_structure_not_imputed":True,
            "diversification_formula":"100*(1-direct_sector_hhi)",
            "top_holdings_concentration_kept_separate":True,
        },
    }
    (audit_dir/"V21_ETF_FUND_STRUCTURE.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    return payload


if __name__=="__main__":
    run()
