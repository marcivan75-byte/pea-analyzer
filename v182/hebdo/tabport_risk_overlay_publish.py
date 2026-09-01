"""Ablation d'un overlay de risque-stop walk-forward sur la baseline TABPORT J1.

Le moteur de ranking, les sorties et la taille des positions restent inchangés.
Seul un veto d'entree est applique lorsque la probabilite calibree de toucher -9%
depasse un seuil fixe predefini. Aucun seuil n'est optimise dans ce module.
"""
from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_publish import read_cache, build_weekly_meta_signals
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary
from v182.hebdo.tabport_walkforward import (
    build_walkforward_candidates, attach_mature_outcomes,
    CalibratedClassifier, _quarter_start,
)

THRESHOLDS=(0.60,0.70,0.80,0.90)


def walkforward_stop_scores(outcomes:pd.DataFrame)->tuple[pd.DataFrame,pd.DataFrame]:
    work=outcomes.copy()
    work['date']=pd.to_datetime(work['date'],utc=True)
    work['outcome_end_date']=pd.to_datetime(work['outcome_end_date'],utc=True)
    work['quarter_start']=work['date'].map(_quarter_start)
    scored=[]; vintages=[]
    for q,score_rows in work.groupby('quarter_start',sort=True):
        hist=work[work['outcome_end_date']<q].copy()
        model=CalibratedClassifier('hit_stop')
        metrics=model.fit(hist)
        row={'quarter_start':str(q),'history_rows':int(len(hist)),'stop_model':metrics,'status':'BLOCKED','scored_rows':0}
        if model.status!='CALIBRATED_PURGED_TEMPORAL_OOS':
            vintages.append(row); continue
        s=score_rows.copy()
        s['prob_stop_9_calibrated']=model.predict(s)
        s['risk_model_vintage']=str(q)
        s['risk_model_status']='CALIBRATED_PURGED_TEMPORAL_OOS'
        row.update({'status':'VALIDATED','scored_rows':int(len(s))})
        vintages.append(row); scored.append(s)
    all_scored=(pd.concat(scored,ignore_index=True).sort_values(['date','ticker']).reset_index(drop=True) if scored else pd.DataFrame())
    return all_scored,pd.DataFrame(vintages)


def _summary(name:str,result:dict,threshold:float|None=None)->dict:
    s=overall_summary(result['ledger'],result['equity'],65000.0)
    s['scenario']=name; s['veto_threshold']=threshold
    return s


def publish(cache_dir:str|Path,output_dir:str|Path)->dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    ohlcv,_=read_cache(cache_dir)
    candidates=build_walkforward_candidates(ohlcv)
    outcomes=attach_mature_outcomes(candidates,ohlcv)
    risk,vintages=walkforward_stop_scores(outcomes)
    vintages.to_csv(out/'TABPORT_RISK_OVERLAY_VINTAGES.csv',index=False)
    if risk.empty:
        raise ValueError('BLOCK_RISK_OVERLAY: no validated stop-risk vintage')
    risk[['date','ticker','prob_stop_9_calibrated','risk_model_vintage','risk_model_status']].to_csv(out/'TABPORT_RISK_OVERLAY_SCORES.csv',index=False)

    first=pd.to_datetime(risk['date'],utc=True).min(); last=pd.to_datetime(risk['date'],utc=True).max()
    base,_=build_weekly_meta_signals(ohlcv)
    base=base[(pd.to_datetime(base['date'],utc=True)>=first)&(pd.to_datetime(base['date'],utc=True)<=last)].copy()
    features=add_antifp_features(ohlcv[ohlcv['ticker'].astype(str).isin(set(base['ticker'].astype(str)))].copy())
    confirmed,audit=apply_j1_confirmation(base,features)
    audit.to_csv(out/'TABPORT_RISK_OVERLAY_CONFIRMATION_AUDIT.csv',index=False)
    if confirmed.empty: raise ValueError('BLOCK_RISK_OVERLAY: baseline confirmation empty')
    confirmed['risk_key_date']=pd.to_datetime(confirmed['original_signal_date'],utc=True)
    rkey=risk[['date','ticker','prob_stop_9_calibrated','risk_model_vintage','risk_model_status']].copy().rename(columns={'date':'risk_key_date'})
    merged=confirmed.merge(rkey,on=['risk_key_date','ticker'],how='left',validate='many_to_one')
    coverage=float(merged['prob_stop_9_calibrated'].notna().mean())
    merged.to_csv(out/'TABPORT_RISK_OVERLAY_BASELINE_CONFIRMED.csv',index=False)
    if coverage<0.95:
        raise ValueError(f'BLOCK_RISK_OVERLAY: calibrated coverage too low {coverage:.2%}')

    plain=ohlcv[['date','ticker','open','high','low','close']].copy(); cfg=TabportConfig()
    baseline=Tabport65k(cfg).run(merged,plain)
    results={'BASELINE_CONFIRM_J1':baseline}; rows=[_summary('BASELINE_CONFIRM_J1',baseline,None)]
    veto_counts={}
    for threshold in THRESHOLDS:
        keep=merged[(merged['prob_stop_9_calibrated'].isna())|(merged['prob_stop_9_calibrated']<threshold)].copy()
        veto=int(len(merged)-len(keep)); veto_counts[f'{threshold:.2f}']=veto
        if keep.empty:
            continue
        result=Tabport65k(cfg).run(keep,plain)
        name=f'RISK_VETO_{threshold:.2f}'
        results[name]=result; rows.append(_summary(name,result,threshold))
        d=out/name.lower(); d.mkdir(parents=True,exist_ok=True)
        result['ledger'].to_csv(d/'ledger.csv',index=False); result['equity'].to_csv(d/'nav.csv',index=False); result['skipped'].to_csv(d/'skipped.csv',index=False)
    baseline['ledger'].to_csv(out/'baseline_ledger.csv',index=False); baseline['equity'].to_csv(out/'baseline_nav.csv',index=False)
    comp=pd.DataFrame(rows); comp.to_csv(out/'TABPORT_RISK_OVERLAY_COMPARISON.csv',index=False)

    b=rows[0]; candidates_diag=[]
    for r in rows[1:]:
        candidates_diag.append({
            'scenario':r['scenario'],'threshold':r['veto_threshold'],
            'return_delta_pct':float(r['rendement_total_depuis_65000_pct']-b['rendement_total_depuis_65000_pct']),
            'win_rate_delta_pct':float(r['taux_gain_pct']-b['taux_gain_pct']),
            'pf_delta':float(r['profit_factor']-b['profit_factor']),
            'rr_delta':float(r['rr_payoff']-b['rr_payoff']),
            'expectancy_delta_pct':float(r['esperance_pct']-b['esperance_pct']),
            'stops_delta':int(r['stops']-b['stops']),
            'drawdown_delta_pct':float(r['drawdown_max_pct']-b['drawdown_max_pct']),
        })
    diag={
        'status':'PUBLISHED','name':'TABPORT_CALIBRATED_STOP_RISK_OVERLAY_ABLATION',
        'retuning':False,'holdout_unlocked':False,'ranking_changed':False,'exit_logic_changed':False,
        'thresholds_tested':list(THRESHOLDS),'coverage_pct':coverage*100,
        'first_score':str(first),'last_score':str(last),'confirmed_baseline_signals':int(len(merged)),
        'veto_counts':veto_counts,'scenarios':rows,'deltas_vs_baseline':candidates_diag,
        'promotion_rule':'No automatic promotion. Prefer an overlay only if stop burden/drawdown improve without material deterioration of PF, RR, expectancy or portfolio return across the development window; final holdout remains locked.'
    }
    (out/'TABPORT_RISK_OVERLAY_DIAGNOSTIC.json').write_text(json.dumps(diag,indent=2,default=str),encoding='utf-8')
    print(json.dumps(diag,default=str)); return diag


def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',default='data/cache/actions'); p.add_argument('--output-dir',default='outputs/tabport_risk_overlay')
    a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=='__main__': main()
