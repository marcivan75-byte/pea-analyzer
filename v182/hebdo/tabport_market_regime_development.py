"""Market-regime guard study for TABPORT, fitted only on 2010-2022.

The regime features use only market data available on the signal's
market_snapshot_date (T-1 to the J+1 decision):
- cross-sectional breadth above each ticker's SMA200;
- cross-sectional breadth with positive 20-session return;
- cross-sectional median 20-session return.

Candidate thresholds are distributional or zero-based, chosen without reading
holdout outcomes. Holdout 2023-2026 is evaluation-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_publish import build_weekly_meta_signals
from v182.hebdo.tabport_regime_guard_development import load_ohlcv, DEV_END, HOLDOUT_START


def market_regime_daily(ohlcv: pd.DataFrame) -> pd.DataFrame:
    x = ohlcv[["date", "ticker", "close"]].copy().sort_values(["ticker", "date"])
    x["close"] = pd.to_numeric(x["close"], errors="coerce")
    g = x.groupby("ticker", sort=False)["close"]
    x["sma200"] = g.transform(lambda s: s.rolling(200, min_periods=200).mean())
    x["ret20"] = g.pct_change(20)
    x["above_sma200"] = np.where(x["sma200"].notna(), x["close"] > x["sma200"], np.nan)
    x["ret20_positive"] = np.where(x["ret20"].notna(), x["ret20"] > 0, np.nan)
    daily = x.groupby("date", as_index=False).agg(
        breadth_sma200=("above_sma200", "mean"),
        breadth_ret20_positive=("ret20_positive", "mean"),
        median_ret20=("ret20", "median"),
        regime_universe=("ticker", "nunique"),
    )
    return daily.sort_values("date").reset_index(drop=True)


def attach_regime(confirmed: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    x = confirmed.copy()
    if "market_snapshot_date" not in x.columns:
        raise ValueError("BLOCK_MARKET_REGIME_MISSING_MARKET_SNAPSHOT_DATE")
    x["market_snapshot_date"] = pd.to_datetime(x["market_snapshot_date"], utc=True, errors="coerce")
    if x["market_snapshot_date"].isna().any():
        raise ValueError("BLOCK_MARKET_REGIME_INVALID_MARKET_SNAPSHOT_DATE")
    d = daily.copy(); d["date"] = pd.to_datetime(d["date"], utc=True, errors="coerce")
    x = x.merge(d, left_on="market_snapshot_date", right_on="date", how="left", suffixes=("", "_regime"))
    need = ["breadth_sma200", "breadth_ret20_positive", "median_ret20"]
    if x[need].isna().any().any():
        raise ValueError("BLOCK_MARKET_REGIME_MISSING_PIT_REGIME_FEATURE")
    decision = pd.to_datetime(x["date_x"] if "date_x" in x.columns else x["date"], utc=True, errors="coerce")
    if (x["market_snapshot_date"] >= decision).any():
        raise ValueError("BLOCK_MARKET_REGIME_LOOKAHEAD")
    if "date_x" in x.columns:
        x = x.rename(columns={"date_x": "date"}).drop(columns=[c for c in ["date_y"] if c in x.columns])
    return x


def learn_thresholds(confirmed: pd.DataFrame) -> dict[str, float]:
    dev = confirmed[pd.to_datetime(confirmed["date"], utc=True) <= DEV_END].copy()
    if dev.empty:
        raise ValueError("BLOCK_MARKET_REGIME_NO_DEVELOPMENT")
    return {
        "breadth_sma200_q40": float(dev["breadth_sma200"].quantile(0.40)),
        "breadth_sma200_q50": float(dev["breadth_sma200"].quantile(0.50)),
        "breadth_ret20_q40": float(dev["breadth_ret20_positive"].quantile(0.40)),
    }


def candidate_masks(x: pd.DataFrame, t: dict[str, float]) -> dict[str, pd.Series]:
    b200 = pd.to_numeric(x["breadth_sma200"], errors="coerce")
    b20 = pd.to_numeric(x["breadth_ret20_positive"], errors="coerce")
    med20 = pd.to_numeric(x["median_ret20"], errors="coerce")
    return {
        "BASELINE": pd.Series(True, index=x.index),
        "BREADTH_SMA200_GE_DEV_Q40": b200.ge(t["breadth_sma200_q40"]),
        "BREADTH_SMA200_GE_DEV_Q50": b200.ge(t["breadth_sma200_q50"]),
        "BREADTH_RET20_GE_DEV_Q40": b20.ge(t["breadth_ret20_q40"]),
        "MEDIAN_RET20_GE_ZERO": med20.ge(0.0),
        "BREADTH200_Q40_AND_MEDIAN_RET20_POS": b200.ge(t["breadth_sma200_q40"]) & med20.ge(0.0),
    }


def run(pre2023: Path, manifest: Path, holdout_cache: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ohlcv, quality = load_ohlcv(pre2023, manifest, holdout_cache)
    signals, signal_audit = build_weekly_meta_signals(ohlcv)
    features = add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy())
    confirmed, confirmation_audit = apply_j1_confirmation(signals, features)
    confirmed = confirmed.reset_index(drop=True)
    daily = market_regime_daily(ohlcv)
    confirmed = attach_regime(confirmed, daily)
    thresholds = learn_thresholds(confirmed)
    masks = candidate_masks(confirmed, thresholds)
    cfg = TabportConfig()
    prices = ohlcv[["date", "ticker", "open", "high", "low", "close"]].copy()

    rows=[]; ledgers=[]; yearly=[]; quarterly=[]
    for model, mask in masks.items():
        chosen = confirmed.loc[mask.fillna(False)].copy()
        result = Tabport65k(cfg).run(chosen, prices)
        ledger=result["ledger"].copy(); nav=result["equity"].copy(); ledger["model"]=model; ledgers.append(ledger)
        ledger["signal_date"] = pd.to_datetime(ledger["signal_date"], utc=True, errors="coerce")
        nav["date"] = pd.to_datetime(nav["date"], utc=True, errors="coerce")
        for segment, lo, hi in [
            ("DEVELOPMENT_2010_2022", pd.Timestamp("2010-01-01", tz="UTC"), DEV_END),
            ("HOLDOUT_2023_2026", HOLDOUT_START, pd.Timestamp("2100-01-01", tz="UTC")),
        ]:
            ls=ledger[(ledger["signal_date"]>=lo)&(ledger["signal_date"]<=hi)].copy()
            ns=nav[(nav["date"]>=lo)&(nav["date"]<=hi)].copy()
            rows.append({"model":model,"segment":segment,"signals_selected":int(((pd.to_datetime(chosen["date"],utc=True)>=lo)&(pd.to_datetime(chosen["date"],utc=True)<=hi)).sum()),**overall_summary(ls,ns,initial_cash=cfg.initial_cash)})
        q=period_table(ledger,nav,"Q"); y=period_table(ledger,nav,"Y")
        if not q.empty: q.insert(0,"model",model); quarterly.append(q)
        if not y.empty: y.insert(0,"model",model); yearly.append(y)

    pd.DataFrame(rows).to_csv(output_dir/"TABPORT_MARKET_REGIME_SEGMENTS.csv",index=False)
    pd.concat(ledgers,ignore_index=True).to_csv(output_dir/"TABPORT_MARKET_REGIME_LEDGERS.csv",index=False)
    pd.concat(yearly,ignore_index=True).to_csv(output_dir/"TABPORT_MARKET_REGIME_YEARLY.csv",index=False)
    pd.concat(quarterly,ignore_index=True).to_csv(output_dir/"TABPORT_MARKET_REGIME_QUARTERLY.csv",index=False)
    confirmed.to_csv(output_dir/"TABPORT_MARKET_REGIME_CONFIRMED.csv",index=False)
    daily.to_csv(output_dir/"TABPORT_MARKET_REGIME_DAILY.csv",index=False)
    confirmation_audit.to_csv(output_dir/"TABPORT_MARKET_REGIME_CONFIRMATION_AUDIT.csv",index=False)
    payload={
        "status":"SUCCESS",
        "version":"TABPORT_MARKET_REGIME_DEV_ONLY_V1",
        "thresholds":thresholds,
        "governance":{
            "fit_window":"2010-2022_ONLY",
            "holdout":"2023-2026_EVALUATION_ONLY",
            "holdout_used_for_threshold_selection":False,
            "regime_timestamp":"MARKET_SNAPSHOT_DATE_T_MINUS_1",
            "synthetic_imputation":False,
            "production_promotion":False,
        },
        "quality":quality,"signal_audit":signal_audit,"models":sorted(masks),
    }
    (output_dir/"TABPORT_MARKET_REGIME_SUMMARY.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    return payload


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--pre2023",required=True); p.add_argument("--manifest",required=True); p.add_argument("--holdout-cache",required=True); p.add_argument("--output-dir",required=True)
    a=p.parse_args(); print(json.dumps(run(Path(a.pre2023),Path(a.manifest),Path(a.holdout_cache),Path(a.output_dir)),indent=2,default=str))

if __name__=="__main__": main()
