"""Capitulation-quality x J+1 confirmation study, development-only fit.

The candidate family is intentionally small and interpretable. Quantile
thresholds are estimated only from 2010-2022 confirmed signals, then frozen.
2023-2026 is evaluation-only and is never used to alter the rules.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_publish import build_weekly_meta_signals
from v182.hebdo.tabport_regime_guard_development import load_ohlcv, DEV_END, HOLDOUT_START


def attach_confirmation_strength(confirmed: pd.DataFrame, ohlcv: pd.DataFrame) -> pd.DataFrame:
    x = confirmed.copy()
    x["date"] = pd.to_datetime(x["date"], utc=True, errors="coerce")
    bars = ohlcv[["date", "ticker", "open", "high", "low", "close", "volume"]].copy()
    bars["date"] = pd.to_datetime(bars["date"], utc=True, errors="coerce")
    bars = bars.rename(columns={
        "open":"j1_open", "high":"j1_high", "low":"j1_low",
        "close":"j1_close", "volume":"j1_volume",
    })
    x = x.merge(bars, on=["date","ticker"], how="left", validate="many_to_one")
    need=["signal_level","j1_open","j1_high","j1_low","j1_close"]
    if x[need].isna().any().any():
        raise ValueError("BLOCK_CAP_CONFIRM_MISSING_J1_BAR")
    x["j1_gap_pct"] = x["j1_open"] / x["signal_level"] - 1.0
    x["j1_ret_close_pct"] = x["j1_close"] / x["signal_level"] - 1.0
    x["j1_intraday_pct"] = x["j1_close"] / x["j1_open"] - 1.0
    x["j1_close_from_low_pct"] = x["j1_close"] / x["j1_low"] - 1.0
    # ConfirmationEntry accepts only ret_close > 0.5%; retain that invariant.
    if (x["j1_ret_close_pct"] <= 0.005).any():
        raise ValueError("BLOCK_CAP_CONFIRM_INCONSISTENT_CONFIRMED_ROW")
    return x


def learn_thresholds(confirmed: pd.DataFrame) -> dict[str,float]:
    dev=confirmed[pd.to_datetime(confirmed["date"],utc=True)<=DEV_END].copy()
    if dev.empty: raise ValueError("BLOCK_CAP_CONFIRM_NO_DEVELOPMENT")
    required=["drawdown_4w","vol_z","prob_stop_9","j1_ret_close_pct","j1_intraday_pct","j1_close_from_low_pct"]
    if any(c not in dev.columns for c in required): raise ValueError("BLOCK_CAP_CONFIRM_MISSING_FEATURE")
    return {
        "drawdown_4w_q40": float(pd.to_numeric(dev["drawdown_4w"],errors="coerce").quantile(0.40)),
        "vol_z_q60": float(pd.to_numeric(dev["vol_z"],errors="coerce").quantile(0.60)),
        "prob_stop_9_q50": float(pd.to_numeric(dev["prob_stop_9"],errors="coerce").quantile(0.50)),
        "j1_ret_close_q50": float(pd.to_numeric(dev["j1_ret_close_pct"],errors="coerce").quantile(0.50)),
        "j1_intraday_q50": float(pd.to_numeric(dev["j1_intraday_pct"],errors="coerce").quantile(0.50)),
        "j1_close_from_low_q50": float(pd.to_numeric(dev["j1_close_from_low_pct"],errors="coerce").quantile(0.50)),
    }


def candidate_masks(x: pd.DataFrame,t:dict[str,float])->dict[str,pd.Series]:
    dd=pd.to_numeric(x["drawdown_4w"],errors="coerce")
    vol=pd.to_numeric(x["vol_z"],errors="coerce")
    ps=pd.to_numeric(x["prob_stop_9"],errors="coerce")
    r=pd.to_numeric(x["j1_ret_close_pct"],errors="coerce")
    intra=pd.to_numeric(x["j1_intraday_pct"],errors="coerce")
    low=pd.to_numeric(x["j1_close_from_low_pct"],errors="coerce")
    strong=r.ge(t["j1_ret_close_q50"])
    deep=dd.le(t["drawdown_4w_q40"])
    return {
        "BASELINE": pd.Series(True,index=x.index),
        "J1_RET_GE_DEV_Q50": strong,
        "DEEP_DD_Q40_AND_J1_RET_Q50": deep & strong,
        "J1_INTRADAY_GE_DEV_Q50": intra.ge(t["j1_intraday_q50"]),
        "DEEP_DD_Q40_AND_J1_CLOSE_FROM_LOW_Q50": deep & low.ge(t["j1_close_from_low_q50"]),
        "VOL_LE_Q60_AND_J1_RET_Q50": vol.le(t["vol_z_q60"]) & strong,
        "PROB_STOP_LE_Q50_AND_J1_RET_Q50": ps.le(t["prob_stop_9_q50"]) & strong,
    }


def run(pre2023:Path,manifest:Path,holdout_cache:Path,output_dir:Path)->dict:
    output_dir.mkdir(parents=True,exist_ok=True)
    ohlcv,quality=load_ohlcv(pre2023,manifest,holdout_cache)
    signals,signal_audit=build_weekly_meta_signals(ohlcv)
    features=add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy())
    confirmed,confirmation_audit=apply_j1_confirmation(signals,features)
    confirmed=attach_confirmation_strength(confirmed.reset_index(drop=True),ohlcv)
    thresholds=learn_thresholds(confirmed)
    masks=candidate_masks(confirmed,thresholds)
    cfg=TabportConfig(); prices=ohlcv[["date","ticker","open","high","low","close"]].copy()
    rows=[]; ledgers=[]; yearly=[]; quarterly=[]
    for model,mask in masks.items():
        chosen=confirmed.loc[mask.fillna(False)].copy()
        if chosen.empty: continue
        result=Tabport65k(cfg).run(chosen,prices)
        ledger=result["ledger"].copy(); nav=result["equity"].copy(); ledger["model"]=model; ledgers.append(ledger)
        ledger["signal_date"]=pd.to_datetime(ledger["signal_date"],utc=True,errors="coerce")
        nav["date"]=pd.to_datetime(nav["date"],utc=True,errors="coerce")
        chosen_dates=pd.to_datetime(chosen["date"],utc=True,errors="coerce")
        for segment,lo,hi in [
            ("DEVELOPMENT_2010_2022",pd.Timestamp("2010-01-01",tz="UTC"),DEV_END),
            ("HOLDOUT_2023_2026",HOLDOUT_START,pd.Timestamp("2100-01-01",tz="UTC")),
        ]:
            ls=ledger[(ledger["signal_date"]>=lo)&(ledger["signal_date"]<=hi)].copy()
            ns=nav[(nav["date"]>=lo)&(nav["date"]<=hi)].copy()
            rows.append({"model":model,"segment":segment,"signals_selected":int(((chosen_dates>=lo)&(chosen_dates<=hi)).sum()),**overall_summary(ls,ns,initial_cash=cfg.initial_cash)})
        q=period_table(ledger,nav,"Q"); y=period_table(ledger,nav,"Y")
        if not q.empty: q.insert(0,"model",model); quarterly.append(q)
        if not y.empty: y.insert(0,"model",model); yearly.append(y)
    pd.DataFrame(rows).to_csv(output_dir/"TABPORT_CAP_CONFIRM_SEGMENTS.csv",index=False)
    pd.concat(ledgers,ignore_index=True).to_csv(output_dir/"TABPORT_CAP_CONFIRM_LEDGERS.csv",index=False)
    pd.concat(yearly,ignore_index=True).to_csv(output_dir/"TABPORT_CAP_CONFIRM_YEARLY.csv",index=False)
    pd.concat(quarterly,ignore_index=True).to_csv(output_dir/"TABPORT_CAP_CONFIRM_QUARTERLY.csv",index=False)
    confirmed.to_csv(output_dir/"TABPORT_CAP_CONFIRM_CONFIRMED.csv",index=False)
    confirmation_audit.to_csv(output_dir/"TABPORT_CAP_CONFIRM_AUDIT.csv",index=False)
    payload={
        "status":"SUCCESS","version":"TABPORT_CAPITULATION_CONFIRM_DEV_ONLY_V1",
        "thresholds":thresholds,
        "governance":{
            "fit_window":"2010-2022_ONLY","holdout":"2023-2026_EVALUATION_ONLY",
            "holdout_used_for_threshold_selection":False,"candidate_family_frozen_before_holdout":True,
            "j1_features_timestamp":"CONFIRMATION_CLOSE_BEFORE_NEXT_SESSION_ENTRY",
            "synthetic_imputation":False,"production_promotion":False,
        },
        "quality":quality,"signal_audit":signal_audit,"models":sorted(masks),
    }
    (output_dir/"TABPORT_CAP_CONFIRM_SUMMARY.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    return payload


def main()->None:
    p=argparse.ArgumentParser(); p.add_argument("--pre2023",required=True); p.add_argument("--manifest",required=True); p.add_argument("--holdout-cache",required=True); p.add_argument("--output-dir",required=True)
    a=p.parse_args(); print(json.dumps(run(Path(a.pre2023),Path(a.manifest),Path(a.holdout_cache),Path(a.output_dir)),indent=2,default=str))

if __name__=="__main__": main()
