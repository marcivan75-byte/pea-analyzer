"""Ablation des tie-breaks lorsque l'EV primaire TABPORT est identique.

La valeur economique EV_net originale n'est pas reparametree. Un epsilon strictement
subordonne a toute difference d'EV sert uniquement a remplacer l'ordre alphabetique
dans les egalites. Les sorties, frais et tailles de position restent identiques.
"""
from __future__ import annotations

import argparse, json, re
from pathlib import Path
import numpy as np
import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_publish import read_cache, build_weekly_meta_signals
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary
from v182.hebdo.tabport_walkforward import build_walkforward_candidates, attach_mature_outcomes
from v182.hebdo.tabport_risk_overlay_publish import walkforward_stop_scores

EPS=1e-7


def _confirm_strength(reason)->float:
    m=re.search(r'CONFIRME_([-+]?\d+(?:\.\d+)?)%',str(reason))
    return float(m.group(1))/100.0 if m else 0.0


def _with_tiebreak(df:pd.DataFrame, mode:str)->pd.DataFrame:
    x=df.copy()
    base=pd.to_numeric(x['EV_net'],errors='coerce')
    if base.isna().any(): raise ValueError('BLOCK_TIEBREAK: non numeric EV')
    if mode=='PROXY_RISK_LOW':
        sec=-pd.to_numeric(x['risk_stop_9_proxy'],errors='coerce').fillna(1.0)
    elif mode=='CALIBRATED_RISK_LOW':
        sec=-pd.to_numeric(x['prob_stop_9_calibrated'],errors='coerce').fillna(1.0)
    elif mode=='CONFIRM_STRENGTH_HIGH':
        sec=x['confirmation_reason'].map(_confirm_strength)
    elif mode=='CAL_RISK_THEN_CONFIRM':
        risk=pd.to_numeric(x['prob_stop_9_calibrated'],errors='coerce').fillna(1.0)
        conf=x['confirmation_reason'].map(_confirm_strength)
        # risque prioritaire; confirmation ne departage que les risques proches.
        sec=(-risk)+(conf*1e-3)
    else:
        raise ValueError(f'BLOCK_TIEBREAK: unknown mode {mode}')
    # normalisation bornee pour que le tie-break ne puisse jamais depasser EPS.
    lo=float(sec.min()); hi=float(sec.max())
    norm=(sec-lo)/(hi-lo) if hi>lo else pd.Series(0.5,index=x.index)
    x['EV_net_original']=base
    x['tiebreak_mode']=mode
    x['tiebreak_secondary']=sec
    x['EV_net']=base+EPS*norm
    return x


def _summary(name,result):
    s=overall_summary(result['ledger'],result['equity'],65000.0); s['scenario']=name; return s


def publish(cache_dir:str|Path,output_dir:str|Path)->dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    ohlcv,_=read_cache(cache_dir)
    candidates=build_walkforward_candidates(ohlcv)
    outcomes=attach_mature_outcomes(candidates,ohlcv)
    risk,_=walkforward_stop_scores(outcomes)
    if risk.empty: raise ValueError('BLOCK_TIEBREAK: no calibrated risk window')
    first=pd.to_datetime(risk['date'],utc=True).min(); last=pd.to_datetime(risk['date'],utc=True).max()

    base,_=build_weekly_meta_signals(ohlcv)
    base=base[(pd.to_datetime(base['date'],utc=True)>=first)&(pd.to_datetime(base['date'],utc=True)<=last)].copy()
    features=add_antifp_features(ohlcv[ohlcv['ticker'].astype(str).isin(set(base['ticker'].astype(str)))].copy())
    confirmed,audit=apply_j1_confirmation(base,features)
    audit.to_csv(out/'TABPORT_TIEBREAK_CONFIRMATION_AUDIT.csv',index=False)
    if confirmed.empty: raise ValueError('BLOCK_TIEBREAK: empty confirmed baseline')
    confirmed['risk_key_date']=pd.to_datetime(confirmed['original_signal_date'],utc=True)
    rkey=risk[['date','ticker','prob_stop_9_calibrated']].copy().rename(columns={'date':'risk_key_date'})
    merged=confirmed.merge(rkey,on=['risk_key_date','ticker'],how='left',validate='many_to_one')
    coverage=float(merged['prob_stop_9_calibrated'].notna().mean())
    if coverage<0.95: raise ValueError(f'BLOCK_TIEBREAK: calibrated coverage {coverage:.2%}')
    merged.to_csv(out/'TABPORT_TIEBREAK_BASELINE_CONFIRMED.csv',index=False)

    plain=ohlcv[['date','ticker','open','high','low','close']].copy(); cfg=TabportConfig()
    scenarios={'BASELINE_TICKER_TIE':merged}
    for mode in ['PROXY_RISK_LOW','CALIBRATED_RISK_LOW','CONFIRM_STRENGTH_HIGH','CAL_RISK_THEN_CONFIRM']:
        scenarios[mode]=_with_tiebreak(merged,mode)
    rows=[]
    for name,sig in scenarios.items():
        result=Tabport65k(cfg).run(sig,plain); rows.append(_summary(name,result))
        d=out/name.lower(); d.mkdir(parents=True,exist_ok=True)
        result['ledger'].to_csv(d/'ledger.csv',index=False); result['equity'].to_csv(d/'nav.csv',index=False); result['skipped'].to_csv(d/'skipped.csv',index=False)
    comp=pd.DataFrame(rows); comp.to_csv(out/'TABPORT_TIEBREAK_COMPARISON.csv',index=False)
    b=rows[0]
    deltas=[]
    for r in rows[1:]:
        deltas.append({'scenario':r['scenario'],
          'return_delta_pct':float(r['rendement_total_depuis_65000_pct']-b['rendement_total_depuis_65000_pct']),
          'win_rate_delta_pct':float(r['taux_gain_pct']-b['taux_gain_pct']),
          'pf_delta':float(r['profit_factor']-b['profit_factor']),
          'rr_delta':float(r['rr_payoff']-b['rr_payoff']),
          'expectancy_delta_pct':float(r['esperance_pct']-b['esperance_pct']),
          'stops_delta':int(r['stops']-b['stops']),
          'drawdown_delta_pct':float(r['drawdown_max_pct']-b['drawdown_max_pct'])})
    ev_unique=int(merged['EV_net'].nunique())
    diag={'status':'PUBLISHED','name':'TABPORT_FLAT_EV_TIEBREAK_ABLATION','retuning':False,'holdout_unlocked':False,
          'epsilon':EPS,'primary_ev_unique_values':ev_unique,'confirmed_signals':int(len(merged)),'coverage_pct':coverage*100,
          'scenarios':rows,'deltas_vs_baseline':deltas,
          'interpretation':'Tie-break epsilon is subordinate to primary EV and only replaces arbitrary ticker ordering when EV values are equal.'}
    (out/'TABPORT_TIEBREAK_DIAGNOSTIC.json').write_text(json.dumps(diag,indent=2,default=str),encoding='utf-8')
    print(json.dumps(diag,default=str)); return diag


def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',default='data/cache/actions'); p.add_argument('--output-dir',default='outputs/tabport_tiebreak')
    a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=='__main__': main()
