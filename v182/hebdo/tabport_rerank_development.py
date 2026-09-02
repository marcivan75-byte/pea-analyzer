"""Development-only cross-sectional reranking study for TABPORT.

Same confirmed signals, same exits, same portfolio constraints. Only candidate
priority changes. Candidate family is frozen before holdout; model selection is
performed on 2010-2022 only, then evaluated once on 2023-2026.
"""
from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_capitulation_confirmation_development import attach_confirmation_strength
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_publish import build_weekly_meta_signals
from v182.hebdo.tabport_regime_guard_development import load_ohlcv, DEV_END, HOLDOUT_START

CANDIDATES = {
    "BASELINE_EV": (1.00, 0.00, 0.00, 0.00),
    "EV_J1": (1.00, 0.25, 0.00, 0.00),
    "EV_RISK": (1.00, 0.00, -0.25, 0.00),
    "EV_J1_RISK": (1.00, 0.25, -0.25, 0.00),
    "EV_J1_VOL": (1.00, 0.25, 0.00, -0.25),
    "EV_BALANCED": (1.00, 0.25, -0.15, -0.10),
}


def _pct_rank(s: pd.Series, ascending: bool = True) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    if x.isna().any():
        raise ValueError("BLOCK_RERANK_MISSING_FEATURE")
    return x.rank(method="average", pct=True, ascending=ascending)


def rerank(x: pd.DataFrame, weights: tuple[float,float,float,float]) -> pd.DataFrame:
    out=x.copy()
    parts=[]
    for _,g in out.groupby("date", sort=False):
        w_ev,w_j1,w_risk,w_vol=weights
        score=(w_ev*_pct_rank(g["EV_net"]) + w_j1*_pct_rank(g["j1_intraday_pct"]) +
               w_risk*_pct_rank(g["prob_stop_9"]) + w_vol*_pct_rank(g["vol_z"]))
        gg=g.copy(); gg["EV_net_original"]=gg["EV_net"]; gg["EV_net"]=1.0+score
        parts.append(gg)
    return pd.concat(parts,ignore_index=True)


def _objective(yearly: pd.DataFrame) -> float:
    y=yearly.copy(); y=y[(y["periode"]>=2011)&(y["periode"]<=2022)]
    if y.empty: return -1e9
    r=pd.to_numeric(y["rendement_portefeuille_pct"],errors="coerce").dropna()
    if r.empty: return -1e9
    return float(r.median() - 0.35*r.std(ddof=0) + 0.20*(r>0).mean()*100.0)


def run(pre2023:Path, manifest:Path, holdout_cache:Path, output_dir:Path)->dict:
    output_dir.mkdir(parents=True,exist_ok=True)
    ohlcv,quality=load_ohlcv(pre2023,manifest,holdout_cache)
    signals,signal_audit=build_weekly_meta_signals(ohlcv)
    features=add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy())
    confirmed,confirmation_audit=apply_j1_confirmation(signals,features)
    confirmed=attach_confirmation_strength(confirmed.reset_index(drop=True),ohlcv)
    cfg=TabportConfig(); prices=ohlcv[["date","ticker","open","high","low","close"]].copy()
    rows=[]; yearly_parts=[]; ledgers=[]; model_dev_scores={}
    for model,w in CANDIDATES.items():
        chosen=rerank(confirmed,w)
        result=Tabport65k(cfg).run(chosen,prices)
        ledger=result["ledger"].copy(); nav=result["equity"].copy(); ledger["model"]=model; ledgers.append(ledger)
        ledger["signal_date"]=pd.to_datetime(ledger["signal_date"],utc=True,errors="coerce")
        nav["date"]=pd.to_datetime(nav["date"],utc=True,errors="coerce")
        y=period_table(ledger,nav,"Y"); y.insert(0,"model",model); yearly_parts.append(y)
        model_dev_scores[model]=_objective(y[y["periode"]<=2022])
        for seg,lo,hi in [("DEVELOPMENT_2010_2022",pd.Timestamp("2010-01-01",tz="UTC"),DEV_END),("HOLDOUT_2023_2026",HOLDOUT_START,pd.Timestamp("2100-01-01",tz="UTC"))]:
            ls=ledger[(ledger["signal_date"]>=lo)&(ledger["signal_date"]<=hi)]
            ns=nav[(nav["date"]>=lo)&(nav["date"]<=hi)]
            rows.append({"model":model,"segment":seg,**overall_summary(ls,ns,initial_cash=cfg.initial_cash)})
    selected=max(model_dev_scores,key=model_dev_scores.get)
    pd.DataFrame(rows).to_csv(output_dir/"TABPORT_RERANK_SEGMENTS.csv",index=False)
    pd.concat(yearly_parts,ignore_index=True).to_csv(output_dir/"TABPORT_RERANK_YEARLY.csv",index=False)
    pd.concat(ledgers,ignore_index=True).to_csv(output_dir/"TABPORT_RERANK_LEDGERS.csv",index=False)
    confirmed.to_csv(output_dir/"TABPORT_RERANK_CONFIRMED.csv",index=False)
    payload={"status":"SUCCESS","version":"TABPORT_RERANK_DEV_ONLY_V1","selected_on_development_only":selected,"development_objective":model_dev_scores,"candidate_weights":CANDIDATES,"governance":{"fit_window":"2010-2022_ONLY","holdout":"2023-2026_EVALUATION_ONLY","holdout_used_for_candidate_or_weight_selection":False,"same_signal_universe":True,"same_exit_rules":True,"production_promotion":False,"synthetic_imputation":False},"quality":quality,"signal_audit":signal_audit}
    (output_dir/"TABPORT_RERANK_SUMMARY.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    return payload


def main():
    p=argparse.ArgumentParser(); p.add_argument("--pre2023",required=True); p.add_argument("--manifest",required=True); p.add_argument("--holdout-cache",required=True); p.add_argument("--output-dir",required=True)
    a=p.parse_args(); print(json.dumps(run(Path(a.pre2023),Path(a.manifest),Path(a.holdout_cache),Path(a.output_dir)),indent=2,default=str))

if __name__=="__main__": main()
