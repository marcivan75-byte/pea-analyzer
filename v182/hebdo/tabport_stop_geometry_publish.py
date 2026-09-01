"""Audit de geometrie des stops TABPORT apres confirmation J+1.

But: distinguer les vrais echecs des titres stoppes a -9% qui recuperent ensuite,
et tester une grille PRE-DECLAREE de largeur de stop sans modifier selection/ranking.
Aucun seuil n'est promu automatiquement; le holdout final reste verrouille.
"""
from __future__ import annotations

import argparse, json
from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_publish import read_cache, build_weekly_meta_signals
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary

STOP_GRID=(0.07,0.09,0.11,0.13,0.15)
BASE_STOP=0.09


def _confirmed_signals(ohlcv:pd.DataFrame)->tuple[pd.DataFrame,pd.DataFrame]:
    signals,_=build_weekly_meta_signals(ohlcv)
    tickers=set(signals['ticker'].astype(str))
    feats=add_antifp_features(ohlcv[ohlcv['ticker'].astype(str).isin(tickers)].copy())
    confirmed,audit=apply_j1_confirmation(signals,feats)
    if confirmed.empty: raise ValueError('BLOCK_STOP_GEOMETRY: no J1-confirmed signals')
    return confirmed,audit


def _trajectory(stops:pd.DataFrame,ohlcv:pd.DataFrame,horizon:int=126)->pd.DataFrame:
    by={str(t):g.sort_values('date').reset_index(drop=True) for t,g in ohlcv.groupby('ticker',sort=False)}
    rows=[]
    for _,r in stops.iterrows():
        ticker=str(r['ticker']); g=by.get(ticker)
        if g is None: continue
        entry_date=pd.to_datetime(r['entry_date'],utc=True); exit_date=pd.to_datetime(r['exit_date'],utc=True); entry=float(r['entry_price'])
        original=g[g['date']>=entry_date].head(horizon)
        after=original[original['date']>exit_date].copy()
        z=r.to_dict(); z['original_horizon_bars']=int(len(original)); z['post_stop_bars']=int(len(after))
        if after.empty:
            z.update({'post_stop_peak_return':np.nan,'post_stop_final_return':np.nan,'recovered_entry':False,
                      'later_plus10':False,'later_plus20':False,'sessions_to_recover':np.nan})
        else:
            peak=float(pd.to_numeric(after['high'],errors='coerce').max()/entry-1)
            final=float(pd.to_numeric(original.iloc[-1]['close'],errors='coerce')/entry-1)
            recover_mask=pd.to_numeric(after['high'],errors='coerce')>=entry
            recover=bool(recover_mask.any())
            sessions=float(np.flatnonzero(recover_mask.to_numpy())[0]+1) if recover else np.nan
            z.update({'post_stop_peak_return':peak,'post_stop_final_return':final,'recovered_entry':recover,
                      'later_plus10':bool(peak>=0.10),'later_plus20':bool(peak>=0.20),'sessions_to_recover':sessions})
        rows.append(z)
    return pd.DataFrame(rows)


def publish(cache_dir:str|Path,output_dir:str|Path)->dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    ohlcv,_=read_cache(cache_dir); confirmed,audit=_confirmed_signals(ohlcv)
    audit.to_csv(out/'TABPORT_STOP_CONFIRMATION_J1_AUDIT.csv',index=False)
    confirmed.to_csv(out/'TABPORT_STOP_CONFIRMED_SIGNALS.csv',index=False)
    plain=ohlcv[['date','ticker','open','high','low','close']].copy(); base_cfg=TabportConfig()
    scenarios=[]; results={}
    for stop in STOP_GRID:
        cfg=replace(base_cfg,stop_pct=float(stop)); res=Tabport65k(cfg).run(confirmed,plain); results[stop]=res
        s=overall_summary(res['ledger'],res['equity'],cfg.initial_cash); s['scenario']=f'STOP_{int(stop*100):02d}PCT'; s['stop_pct']=stop
        scenarios.append(s)
        d=out/f'stop_{int(stop*100):02d}'; d.mkdir(parents=True,exist_ok=True); res['ledger'].to_csv(d/'ledger.csv',index=False); res['equity'].to_csv(d/'nav.csv',index=False)
    comp=pd.DataFrame(scenarios); comp.to_csv(out/'TABPORT_STOP_WIDTH_COMPARISON.csv',index=False)
    base=results[BASE_STOP]; stops=base['ledger'][base['ledger']['exit_reason'].astype(str).str.startswith('STOP')].copy()
    traj=_trajectory(stops,ohlcv,base_cfg.max_hold_sessions); traj.to_csv(out/'TABPORT_STOP_POST_TRAJECTORY.csv',index=False)
    n=int(len(traj)); recovered=int(traj['recovered_entry'].sum()) if n else 0; plus10=int(traj['later_plus10'].sum()) if n else 0; plus20=int(traj['later_plus20'].sum()) if n else 0
    final_positive=int((pd.to_numeric(traj['post_stop_final_return'],errors='coerce')>0).sum()) if n else 0
    never_recover=int(n-recovered)
    baseline=next(x for x in scenarios if abs(x['stop_pct']-BASE_STOP)<1e-12)
    deltas=[]
    for s in scenarios:
        if abs(s['stop_pct']-BASE_STOP)<1e-12: continue
        deltas.append({'scenario':s['scenario'],'stop_pct':s['stop_pct'],
            'return_delta_pct':float(s['rendement_total_depuis_65000_pct']-baseline['rendement_total_depuis_65000_pct']),
            'win_rate_delta_pct':float(s['taux_gain_pct']-baseline['taux_gain_pct']),
            'pf_delta':float(s['profit_factor']-baseline['profit_factor']),
            'rr_delta':float(s['rr_payoff']-baseline['rr_payoff']),
            'expectancy_delta_pct':float(s['esperance_pct']-baseline['esperance_pct']),
            'stops_delta':int(s['stops']-baseline['stops']),
            'drawdown_delta_pct':float(s['drawdown_max_pct']-baseline['drawdown_max_pct'])})
    diag={'status':'PUBLISHED','name':'TABPORT_STOP_GEOMETRY_AND_WIDTH_ABLATION','retuning':False,'holdout_unlocked':False,
          'selection_changed':False,'ranking_changed':False,'stop_grid':list(STOP_GRID),'baseline_stop':BASE_STOP,
          'confirmed_signals':int(len(confirmed)),'baseline_stopped_trades':n,
          'post_stop_trajectory':{'recovered_entry':recovered,'recovered_entry_pct':100*recovered/n if n else 0,
                                  'later_plus10':plus10,'later_plus10_pct':100*plus10/n if n else 0,
                                  'later_plus20':plus20,'later_plus20_pct':100*plus20/n if n else 0,
                                  'positive_at_original_126_close':final_positive,'never_recovered_entry':never_recover},
          'scenarios':scenarios,'deltas_vs_9pct':deltas,
          'interpretation_rule':'A stop is not automatically a false positive: post-stop recovery is reported separately from portfolio economics. Wider/narrower stops are rejected unless PF/RR/expectancy/return and drawdown trade-off improve without hidden selection changes.'}
    (out/'TABPORT_STOP_GEOMETRY_DIAGNOSTIC.json').write_text(json.dumps(diag,indent=2,default=str),encoding='utf-8')
    print(json.dumps(diag,default=str)); return diag


def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',default='data/cache/actions'); p.add_argument('--output-dir',default='outputs/tabport_stop_geometry'); a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=='__main__': main()
