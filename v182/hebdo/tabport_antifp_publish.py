"""Publie une matrice d'ablation anti-FP sur le même univers/historique TABPORT.

Aucune règle n'est recalibrée ici. Les règles historiques existantes sont activées/désactivées
pour mesurer leur contribution sans changer leurs seuils.
"""
from __future__ import annotations

import argparse, json
from pathlib import Path
import pandas as pd

from v182.hebdo.fp_early_exit import FPEarlyExit
from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_publish import read_cache, build_weekly_meta_signals
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation, TabportAntiFP65k
from v182.hebdo.tabport_enriched import overall_summary


def _scenario_summary(name:str, result:dict)->dict:
    ledger=result["ledger"].copy(); nav=result["equity"].copy()
    s=overall_summary(ledger,nav,initial_cash=65000.0); s["scenario"]=name
    er=ledger["exit_reason"].astype(str) if not ledger.empty else pd.Series(dtype=str)
    s.update({
        "stop_final":int(er.str.startswith("STOP").sum()) if len(er) else 0,
        "fail_fast_j2":int(er.str.startswith("FAIL_FAST_J2").sum()) if len(er) else 0,
        "structure_invalid_j2":int(er.str.startswith("STRUCTURE_INVALID_ENTRY_DAY").sum()) if len(er) else 0,
        "mom_dead_j3":int(er.str.startswith("MOM_DEAD_RSI").sum()) if len(er) else 0,
        "capitulation":int(er.str.startswith("CAPITULATION").sum()) if len(er) else 0,
        "trailing_be":int(er.str.startswith("TRAIL_BE").sum()) if len(er) else 0,
        "time_decay_j10":int(er.str.startswith("TIME_DECAY_J10").sum()) if len(er) else 0,
    })
    return s


def _write_scenario(out:Path,name:str,result:dict)->None:
    d=out/name.lower(); d.mkdir(parents=True,exist_ok=True)
    result["ledger"].to_csv(d/"ledger.csv",index=False)
    result["equity"].to_csv(d/"nav.csv",index=False)
    result["skipped"].to_csv(d/"skipped.csv",index=False)


def _engine(cfg:TabportConfig,rules:set[str])->TabportAntiFP65k:
    e=TabportAntiFP65k(cfg)
    e.fp_exit=FPEarlyExit(stop_final=-cfg.stop_pct,enabled_rules=rules)
    return e


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

    confirmed,confirm_audit=apply_j1_confirmation(signals,features)
    if confirmed.empty: raise ValueError("BLOCK_TABPORT_ANTIFP: confirmation rejected every signal")

    results={
        "BASELINE":Tabport65k(cfg).run(signals,plain),
        "CONFIRM_J1_ONLY":Tabport65k(cfg).run(confirmed,plain),
        "CONFIRM_FAIL_FAST":_engine(cfg,{"STOP","FAIL_FAST_J2"}).run(confirmed,antifp_prices),
        "CONFIRM_STRUCTURE_J2":_engine(cfg,{"STOP","STRUCTURE_INVALID_ENTRY_DAY"}).run(confirmed,antifp_prices),
        "CONFIRM_TRAIL_BE":_engine(cfg,{"STOP","TRAIL_BE"}).run(confirmed,antifp_prices),
        "CONFIRM_MOM_DEAD_J3":_engine(cfg,{"STOP","MOM_DEAD_J3"}).run(confirmed,antifp_prices),
        "CONFIRM_FAIL_FAST_TRAIL":_engine(cfg,{"STOP","FAIL_FAST_J2","TRAIL_BE"}).run(confirmed,antifp_prices),
        "CONFIRM_FAIL_FAST_MOM":_engine(cfg,{"STOP","FAIL_FAST_J2","MOM_DEAD_J3"}).run(confirmed,antifp_prices),
        "FULL_ANTIFP":_engine(cfg,set(FPEarlyExit.ALL_RULES)).run(confirmed,antifp_prices),
    }
    rows=[]
    for name,res in results.items():
        _write_scenario(out,name,res); rows.append(_scenario_summary(name,res))
    comp=pd.DataFrame(rows)
    comp.to_csv(out/"TABPORT_ANTIFP_COMPARAISON.csv",index=False)
    confirm_audit.to_csv(out/"TABPORT_CONFIRMATION_J1_AUDIT.csv",index=False)
    confirmed.to_csv(out/"TABPORT_SIGNALS_CONFIRMES.csv",index=False)

    confirm_base=next(r for r in rows if r["scenario"]=="CONFIRM_J1_ONLY")
    candidates=[]
    for r in rows:
        if r["scenario"] in {"BASELINE","CONFIRM_J1_ONLY","FULL_ANTIFP"}: continue
        candidates.append({
            "scenario":r["scenario"],
            "return_delta_vs_confirm_pct":float(r["rendement_total_depuis_65000_pct"]-confirm_base["rendement_total_depuis_65000_pct"]),
            "stops_delta_vs_confirm":int(r["stops"]-confirm_base["stops"]),
            "pf_delta_vs_confirm":float(r["profit_factor"]-confirm_base["profit_factor"]),
            "rr_delta_vs_confirm":float(r["rr_payoff"]-confirm_base["rr_payoff"]),
            "expectancy_delta_vs_confirm_pct":float(r["esperance_pct"]-confirm_base["esperance_pct"]),
        })
    diagnostic={
        "status":"PUBLISHED","name":"TABPORT_ANTIFP_RULE_ABLATION","retuning":False,"synthetic_fallback":False,
        "preopen_historical":"UNAVAILABLE_NOT_SIMULATED","sector_historical":"UNAVAILABLE_NOT_SIMULATED",
        "signal_audit":signal_audit,
        "confirmation_counts":{str(k):int(v) for k,v in confirm_audit["status"].value_counts(dropna=False).to_dict().items()},
        "scenarios":rows,"rule_candidates_vs_confirmation":candidates,
        "selection_policy":"No automatic threshold tuning; retain only pre-existing rules showing lower stop burden with acceptable PF/RR/expectancy/return trade-off on development history. Final holdout remains untouched.",
    }
    (out/"TABPORT_ANTIFP_DIAGNOSTIC.json").write_text(json.dumps(diagnostic,indent=2,default=str),encoding="utf-8")
    print(json.dumps(diagnostic,default=str)); return diagnostic


def main():
    p=argparse.ArgumentParser(); p.add_argument("--cache",default="data/cache/actions"); p.add_argument("--output-dir",default="outputs/tabport_antifp")
    a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=="__main__": main()
