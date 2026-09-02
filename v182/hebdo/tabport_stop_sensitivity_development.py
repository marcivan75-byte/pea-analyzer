"""Development-only stop sensitivity study for TABPORT HEBDO AT META."""
from __future__ import annotations
import argparse, json
from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd
from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_publish import build_weekly_meta_signals
from v182.hebdo.tabport_regime_guard_development import load_ohlcv, DEV_END, HOLDOUT_START

STOPS={"STOP_7":0.07,"STOP_8":0.08,"STOP_9_BASELINE":0.09,"STOP_10":0.10,"STOP_11":0.11}

def _year_number(s): return pd.to_numeric(s.astype(str).str.extract(r"(\d{4})",expand=False),errors="coerce")

def objective(yearly, summary):
    y=yearly.copy(); years=_year_number(y["periode"]); y=y[(years>=2011)&(years<=2022)]
    r=pd.to_numeric(y["rendement_portefeuille_pct"],errors="coerce").dropna()
    vals=[summary.get("drawdown_max_pct"),summary.get("profit_factor"),summary.get("rr_payoff"),summary.get("stop_rate_pct")]
    if r.empty or not all(np.isfinite(float(v)) for v in vals): return -1e9
    dd,pf,rr,sr=map(float,vals)
    return float(r.median()-0.35*r.std(ddof=0)+0.20*(r>0).mean()*100-0.20*abs(dd)+1.5*(pf-1)+0.75*(rr-2)-0.03*sr)

def candidate_config(base, stop):
    if not np.isfinite(stop) or not (0.03<=stop<=0.20): raise ValueError("BLOCK_STOP_INVALID")
    return replace(base,stop_pct=float(stop))

def run(pre2023,manifest,holdout_cache,output_dir):
    output_dir.mkdir(parents=True,exist_ok=True)
    ohlcv,quality=load_ohlcv(pre2023,manifest,holdout_cache)
    signals,signal_audit=build_weekly_meta_signals(ohlcv)
    features=add_antifp_features(ohlcv[ohlcv["ticker"].isin(set(signals["ticker"]))].copy())
    confirmed,confirmation_audit=apply_j1_confirmation(signals,features)
    if confirmed.empty: raise ValueError("BLOCK_STOP_NO_SIGNALS")
    prices=ohlcv[["date","ticker","open","high","low","close"]].copy(); base=TabportConfig()
    rows=[]; yearly_parts=[]; quarterly_parts=[]; ledgers=[]; scores={}; counts={}
    for model,stop in STOPS.items():
        cfg=candidate_config(base,stop); result=Tabport65k(cfg).run(confirmed,prices)
        ledger=result["ledger"].copy(); nav=result["equity"].copy(); ledger["model"]=model; ledgers.append(ledger)
        ledger["signal_date"]=pd.to_datetime(ledger["signal_date"],utc=True,errors="coerce"); nav["date"]=pd.to_datetime(nav["date"],utc=True,errors="coerce")
        y=period_table(ledger,nav,"Y"); y.insert(0,"model",model); yearly_parts.append(y)
        q=period_table(ledger,nav,"Q"); q.insert(0,"model",model); quarterly_parts.append(q)
        dl=ledger[ledger["signal_date"]<=DEV_END]; dn=nav[nav["date"]<=DEV_END]
        ds=overall_summary(dl,dn,initial_cash=cfg.initial_cash); scores[model]=objective(y,ds); counts[model]=len(confirmed)
        for seg,lo,hi in [("DEVELOPMENT_2010_2022",pd.Timestamp("2010-01-01",tz="UTC"),DEV_END),("HOLDOUT_2023_2026",HOLDOUT_START,pd.Timestamp("2100-01-01",tz="UTC"))]:
            ls=ledger[(ledger["signal_date"]>=lo)&(ledger["signal_date"]<=hi)]; ns=nav[(nav["date"]>=lo)&(nav["date"]<=hi)]
            rows.append({"model":model,"stop_pct":stop*100,"segment":seg,**overall_summary(ls,ns,initial_cash=cfg.initial_cash)})
    if len(set(counts.values()))!=1: raise ValueError("BLOCK_STOP_SIGNAL_UNIVERSE_CHANGED")
    selected=max(scores,key=scores.get)
    pd.DataFrame(rows).to_csv(output_dir/"TABPORT_STOP_SEGMENTS.csv",index=False)
    pd.concat(yearly_parts,ignore_index=True).to_csv(output_dir/"TABPORT_STOP_YEARLY.csv",index=False)
    pd.concat(quarterly_parts,ignore_index=True).to_csv(output_dir/"TABPORT_STOP_QUARTERLY.csv",index=False)
    pd.concat(ledgers,ignore_index=True).to_csv(output_dir/"TABPORT_STOP_LEDGERS.csv",index=False)
    payload={"status":"SUCCESS","version":"TABPORT_STOP_DEV_ONLY_V1","selected_on_development_only":selected,"development_objective":scores,"stops":STOPS,"governance":{"fit_window":"2010-2022_ONLY","holdout":"2023-2026_EVALUATION_ONLY","holdout_used_for_stop_selection":False,"candidate_family_frozen_before_holdout":True,"same_signal_universe":True,"same_ranking":True,"same_position_budget_eur":base.max_position_eur,"same_hold_horizon_sessions":base.max_hold_sessions,"only_parameter_changed":"stop_pct","production_promotion":False,"synthetic_imputation":False},"quality":quality,"signal_audit":signal_audit}
    (output_dir/"TABPORT_STOP_SUMMARY.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    return payload

def main():
    p=argparse.ArgumentParser(); p.add_argument("--pre2023",required=True); p.add_argument("--manifest",required=True); p.add_argument("--holdout-cache",required=True); p.add_argument("--output-dir",required=True)
    a=p.parse_args(); print(json.dumps(run(Path(a.pre2023),Path(a.manifest),Path(a.holdout_cache),Path(a.output_dir)),indent=2,default=str))
if __name__=="__main__": main()
