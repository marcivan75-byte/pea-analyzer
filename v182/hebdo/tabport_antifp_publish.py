"""Publie une matrice d'ablation anti-FP sur le même univers/historique TABPORT.

Scénarios fixes, sans retuning:
1. BASELINE
2. EARLY_EXIT_ONLY
3. CONFIRM_J1_ONLY
4. FULL_ANTIFP = confirmation J1 + early exits
"""
from __future__ import annotations

import argparse, json
from pathlib import Path
import pandas as pd
import numpy as np

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_publish import read_cache, build_weekly_meta_signals
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation, TabportAntiFP65k
from v182.hebdo.tabport_enriched import overall_summary


def _scenario_summary(name:str, result:dict)->dict:
    ledger=result["ledger"].copy(); nav=result["equity"].copy()
    s=overall_summary(ledger,nav,initial_cash=65000.0); s["scenario"]=name
    if ledger.empty:
        s.update({"stop_final":0,"fail_fast_j2":0,"mom_dead_j3":0,"capitulation":0,"trailing_be":0,"time_decay_j10":0})
        return s
    er=ledger["exit_reason"].astype(str)
    s.update({
        "stop_final":int(er.str.startswith("STOP").sum()),
        "fail_fast_j2":int(er.str.startswith("FAIL_FAST_J2").sum()),
        "mom_dead_j3":int(er.str.startswith("MOM_DEAD_RSI").sum()),
        "capitulation":int(er.str.startswith("CAPITULATION").sum()),
        "trailing_be":int(er.str.startswith("TRAIL_BE").sum()),
        "time_decay_j10":int(er.str.startswith("TIME_DECAY_J10").sum()),
    })
    return s


def _write_scenario(out:Path,name:str,result:dict)->None:
    d=out/name.lower(); d.mkdir(parents=True,exist_ok=True)
    result["ledger"].to_csv(d/"ledger.csv",index=False)
    result["equity"].to_csv(d/"nav.csv",index=False)
    result["skipped"].to_csv(d/"skipped.csv",index=False)


def publish(cache_dir:str|Path,output_dir:str|Path)->dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    ohlcv,_=read_cache(cache_dir)
    signals,signal_audit=build_weekly_meta_signals(ohlcv)
    tickers=set(signals["ticker"].astype(str))
    raw=ohlcv[ohlcv["ticker"].astype(str).isin(tickers)].copy()
    features=add_antifp_features(raw)
    plain=features[["date","ticker","open","high","low","close"]].copy()
    antifp_prices=features[["date","ticker","open","high","low","close","vol_z","rsi_14"]].copy()
    cfg=TabportConfig()

    baseline=Tabport65k(cfg).run(signals,plain)
    early=TabportAntiFP65k(cfg).run(signals,antifp_prices)
    confirmed,confirm_audit=apply_j1_confirmation(signals,features)
    if confirmed.empty:
        raise ValueError("BLOCK_TABPORT_ANTIFP: confirmation rejected every signal")
    confirm_only=Tabport65k(cfg).run(confirmed,plain)
    full=TabportAntiFP65k(cfg).run(confirmed,antifp_prices)

    results={"BASELINE":baseline,"EARLY_EXIT_ONLY":early,"CONFIRM_J1_ONLY":confirm_only,"FULL_ANTIFP":full}
    rows=[]
    for name,res in results.items():
        _write_scenario(out,name,res); rows.append(_scenario_summary(name,res))
    comp=pd.DataFrame(rows)
    comp.to_csv(out/"TABPORT_ANTIFP_COMPARAISON.csv",index=False)
    confirm_audit.to_csv(out/"TABPORT_CONFIRMATION_J1_AUDIT.csv",index=False)
    confirmed.to_csv(out/"TABPORT_SIGNALS_CONFIRMES.csv",index=False)

    status_counts=confirm_audit["status"].value_counts(dropna=False).to_dict() if not confirm_audit.empty else {}
    base=rows[0]; full_s=rows[-1]
    diagnostic={
        "status":"PUBLISHED",
        "name":"TABPORT_ANTIFP_ABLATION",
        "retuning":False,
        "synthetic_fallback":False,
        "preopen_historical":"UNAVAILABLE_NOT_SIMULATED",
        "sector_historical":"UNAVAILABLE_NOT_SIMULATED",
        "signal_audit":signal_audit,
        "confirmation_counts":{str(k):int(v) for k,v in status_counts.items()},
        "scenarios":rows,
        "full_vs_baseline":{
            "trades_delta":int(full_s["trades"]-base["trades"]),
            "stops_delta":int(full_s["stops"]-base["stops"]),
            "win_rate_delta_pct":float(full_s["taux_gain_pct"]-base["taux_gain_pct"]),
            "profit_factor_delta":None if any(pd.isna([full_s["profit_factor"],base["profit_factor"]])) else float(full_s["profit_factor"]-base["profit_factor"]),
            "rr_delta":None if any(pd.isna([full_s["rr_payoff"],base["rr_payoff"]])) else float(full_s["rr_payoff"]-base["rr_payoff"]),
            "return_delta_pct":None if any(pd.isna([full_s["rendement_total_depuis_65000_pct"],base["rendement_total_depuis_65000_pct"]])) else float(full_s["rendement_total_depuis_65000_pct"]-base["rendement_total_depuis_65000_pct"]),
        },
    }
    (out/"TABPORT_ANTIFP_DIAGNOSTIC.json").write_text(json.dumps(diagnostic,indent=2,default=str),encoding="utf-8")
    print(json.dumps(diagnostic,default=str))
    return diagnostic


def main():
    p=argparse.ArgumentParser(); p.add_argument("--cache",default="data/cache/actions"); p.add_argument("--output-dir",default="outputs/tabport_antifp")
    a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=="__main__": main()
