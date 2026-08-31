from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_canonical(root: Path) -> pd.DataFrame:
    p = next(root.rglob("PEA_OHLCV_2010_2019_CANONICAL.parquet"))
    x = pd.read_parquet(p).copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x = x.rename(columns={"open_raw":"open","high_raw":"high","low_raw":"low","close_raw":"close"})
    x["origin"] = "canonical_2010_2019"
    keep = ["date","asset_class","isin","ticker","open","high","low","close","adj_close","volume","dividends","stock_splits","origin"]
    for c in keep:
        if c not in x.columns:
            x[c] = np.nan
    return x[keep]


def read_recent(root: Path) -> pd.DataFrame:
    p = next(root.rglob("V22_1_OHLCV_2018_2026.parquet"))
    i = next(root.rglob("V22_1_VALIDATED_IDENTITIES.csv"))
    x = pd.read_parquet(p).copy()
    ids = pd.read_csv(i, dtype=str)[["isin","ticker"]].drop_duplicates("ticker")
    x["ticker"] = x["ticker"].astype(str).str.upper().str.strip()
    ids["ticker"] = ids["ticker"].astype(str).str.upper().str.strip()
    x = x.merge(ids, on="ticker", how="left", validate="many_to_one")
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x["asset_class"] = "ACTION"
    x["origin"] = "v22_1_2018_2026"
    for c in ("adj_close","dividends","stock_splits"):
        if c not in x.columns:
            x[c] = np.nan
    keep = ["date","asset_class","isin","ticker","open","high","low","close","adj_close","volume","dividends","stock_splits","origin"]
    return x[keep]


def build_pit(actions: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    grid=pd.date_range("2010-01-01","2026-08-28",freq="W-FRI")
    for (isin,ticker),g0 in actions.groupby(["isin","ticker"],sort=False):
        g=g0.sort_values("date").drop_duplicates("date",keep="last").set_index("date")
        close=pd.to_numeric(g["close"],errors="coerce")
        vol=pd.to_numeric(g["volume"],errors="coerce")
        prior_vol=vol.shift(1)
        avg20=prior_vol.rolling(20,min_periods=20).mean(); std20=prior_vol.rolling(20,min_periods=20).std(ddof=0)
        g["vol_z"]=(vol-avg20)/std20.replace(0,np.nan)
        g["sma20"]=close.rolling(20,min_periods=20).mean(); g["sma200"]=close.rolling(200,min_periods=200).mean()
        g["drawdown_4w"]=close/close.rolling(20,min_periods=20).max()-1
        g["mom_26w"]=close/close.shift(126)-1
        prev=close.shift(1)
        tr=pd.concat([(g["high"]-g["low"]).abs(),(g["high"]-prev).abs(),(g["low"]-prev).abs()],axis=1).max(axis=1)
        g["atr_14_pct"]=tr.rolling(14,min_periods=14).mean()/close.replace(0,np.nan)
        weekly=close.resample("W-FRI").last().dropna(); d=weekly.diff(); gain=d.clip(lower=0).rolling(14,min_periods=14).mean(); loss=(-d.clip(upper=0)).rolling(14,min_periods=14).mean(); rs=gain/loss.replace(0,np.nan)
        wrsi=100-100/(1+rs); g["rsi_14_hebdo"]=wrsi.reindex(g.index,method="ffill")
        g["adv_20_eur"]=(close.shift(1)*vol.shift(1)).rolling(20,min_periods=20).mean()
        pos=g.index.searchsorted(grid,side="right")-1; valid=pos>=0
        if not valid.any(): continue
        take=g.iloc[pos[valid]].copy(); dates=grid[valid]
        take["signal_date"]=dates.values; take["as_of_date"]=(dates+pd.Timedelta(hours=23,minutes=59,seconds=59)).values
        take["market_data_date"]=pd.to_datetime(take.index).values; take["pit_observed_at"]=[f"{d.date().isoformat()}T23:59:59Z" for d in dates]
        take["isin"]=isin; take["ticker"]=ticker
        cols=["isin","ticker","signal_date","as_of_date","market_data_date","pit_observed_at","close","sma20","sma200","vol_z","drawdown_4w","mom_26w","atr_14_pct","rsi_14_hebdo","adv_20_eur"]
        rows.append(take.reset_index(drop=True)[cols])
    if not rows: raise RuntimeError("TECHNICAL_PIT_EMPTY")
    return pd.concat(rows,ignore_index=True)


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--inputs",type=Path,required=True); ap.add_argument("--out",type=Path,required=True); a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    old=read_canonical(a.inputs); new=read_recent(a.inputs)
    new=new[new["isin"].notna()].copy()
    merged=pd.concat([old,new],ignore_index=True)
    merged=merged[(merged["date"]>=pd.Timestamp("2010-01-01"))&(merged["date"]<=pd.Timestamp("2026-08-31"))].copy()
    merged["priority"]=merged["origin"].map({"canonical_2010_2019":0,"v22_1_2018_2026":1}).fillna(0)
    merged=merged.sort_values(["isin","date","priority"]).drop_duplicates(["isin","date"],keep="last").drop(columns="priority")
    critical=merged[["isin","ticker","date","close"]].isna().any(axis=1)
    bad_ohl=(merged["high"]<merged[["open","close","low"]].max(axis=1))|(merged["low"]>merged[["open","close","high"]].min(axis=1))
    nonpositive=(pd.to_numeric(merged["close"],errors="coerce")<=0)
    duplicate=int(merged.duplicated(["isin","date"]).sum())
    audit={"status":"PASS","period":[str(merged.date.min().date()),str(merged.date.max().date())],"rows":int(len(merged)),"unique_isin":int(merged["isin"].nunique()),"unique_tickers":int(merged.ticker.nunique()),"duplicate_isin_date":duplicate,"critical_null_rows":int(critical.sum()),"ohl_inconsistent_rows":int(bad_ohl.fillna(False).sum()),"nonpositive_close_rows":int(nonpositive.fillna(False).sum()),"overlap_policy":"V22.1 recent source wins on duplicate ISIN/date in 2018-2019","pit_classification":"PRICE_ONLY_TECHNICAL_RECONSTRUCTION","survivorship_bias":True,"current_fundamentals_used_as_history":False,"future_returns_embedded":False}
    if duplicate or audit["critical_null_rows"] or audit["nonpositive_close_rows"]: audit["status"]="FAIL"
    annual=[]
    for y,g in merged.groupby(merged.date.dt.year): annual.append({"year":int(y),"rows":int(len(g)),"isins":int(g["isin"].nunique()),"actions":int(g.loc[g.asset_class.eq("ACTION"),"isin"].nunique()),"etfs":int(g.loc[g.asset_class.eq("ETF"),"isin"].nunique())})
    outp=a.out/"PEA_OHLCV_2010_2026_CONSOLIDATED.parquet"; merged.to_parquet(outp,index=False,compression="zstd")
    pd.DataFrame(annual).to_csv(a.out/"PEA_OHLCV_2010_2026_ANNUAL_COVERAGE.csv",index=False)
    (a.out/"PEA_OHLCV_2010_2026_AUDIT.json").write_text(json.dumps(audit,indent=2),encoding="utf-8")
    actions=merged[merged.asset_class.eq("ACTION")].copy(); pit=build_pit(actions); pit.to_parquet(a.out/"PEA_TECHNICAL_PIT_2010_2026.parquet",index=False,compression="zstd")
    manifest={"version":"PEA_HISTORY_2010_2026_V1","audit":audit,"technical_pit_rows":int(len(pit)),"technical_pit_isins":int(pit["isin"].nunique()),"limits":["Current governed universe reconstruction creates survivorship bias.","Only historical price/volume technical PIT is certified before separate dated non-price evidence exists.","No current fundamentals, consensus or sector scores are backfilled historically."],"files":{}}
    for p in [outp,a.out/"PEA_OHLCV_2010_2026_ANNUAL_COVERAGE.csv",a.out/"PEA_OHLCV_2010_2026_AUDIT.json",a.out/"PEA_TECHNICAL_PIT_2010_2026.parquet"]: manifest["files"][p.name]={"size_bytes":p.stat().st_size,"sha256":sha256(p)}
    (a.out/"MANIFEST.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps(manifest,indent=2))
    if audit["status"]!="PASS": raise RuntimeError("CONSOLIDATED_HISTORY_AUDIT_FAILED")
    return 0

if __name__=="__main__": raise SystemExit(main())
