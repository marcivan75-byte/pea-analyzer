"""Research-only V9 diagnostic: explain P2 stops vs D winners without changing execution.

This stage does not create a new trading rule. It freezes P2_SCORE88_CD12 from V8,
reconstructs the selected trades with PIT completed-week features, and compares
PROTECTIVE_STOP exits with D_REVERSAL exits. The purpose is to identify candidate
ex-ante discriminants for a later pre-declared validation stage, not to fit a rule
against outcomes here.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_selection_quality_v8_top30 import POLICIES, select
from .at_weekly_growth_potential_pit_v1 import enrich_trades
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_SELECTION_DIAGNOSTIC_V9_P2.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_SELECTION_DIAGNOSTIC_V9_P2.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_SELECTION_DIAGNOSTIC_V9_P2_TRADES.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_SELECTION_DIAGNOSTIC_V9_P2.md'
P2=next(p for p in POLICIES if p['name']=='P2_SCORE88_CD12')
FEATURES=['quality_score_v8','momentum_12w_pct','momentum_26w_pct','acceleration_4w_pct',
          'breakout_above_prior_52w_high_pct','upside_to_prior_52w_high_pct',
          'distance_sma20_pct','distance_sma50_pct','rsi14','stoch_k']

def stat(a):
    a=pd.to_numeric(a,errors='coerce').dropna()
    if a.empty:return {'n':0,'mean':None,'median':None,'p25':None,'p75':None}
    return {'n':int(len(a)),'mean':round(float(a.mean()),3),'median':round(float(a.median()),3),
            'p25':round(float(a.quantile(.25)),3),'p75':round(float(a.quantile(.75)),3)}

def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any():
        raise RuntimeError('lookahead feature timestamp')
    p2=select(df,P2).copy()
    p2['outcome_group']=np.where(p2.exit_category.eq('PROTECTIVE_STOP'),'STOP',
                         np.where(p2.exit_category.eq('D_REVERSAL'),'D_WINNER','OTHER'))
    stops=p2[p2.outcome_group.eq('STOP')]; dw=p2[p2.outcome_group.eq('D_WINNER')]
    rows=[]
    for f in FEATURES:
        ss=stat(stops[f]); ds=stat(dw[f])
        sm=ss['median']; dm=ds['median']
        delta=None if sm is None or dm is None else round(float(dm-sm),3)
        pooled=pd.to_numeric(p2[f],errors='coerce').std(ddof=0)
        effect=None if delta is None or not np.isfinite(pooled) or pooled==0 else round(float(delta/pooled),3)
        rows.append({'feature':f,'stop_n':ss['n'],'stop_mean':ss['mean'],'stop_median':sm,'stop_p25':ss['p25'],'stop_p75':ss['p75'],
                     'd_n':ds['n'],'d_mean':ds['mean'],'d_median':dm,'d_p25':ds['p25'],'d_p75':ds['p75'],
                     'd_minus_stop_median':delta,'standardized_median_gap':effect})
    res=pd.DataFrame(rows)
    res['abs_gap']=res.standardized_median_gap.abs()
    res=res.sort_values(['abs_gap','feature'],ascending=[False,True]).drop(columns='abs_gap')
    errors=[]
    if len(p2)!=28: errors.append(f'expected frozen P2 28 trades, got {len(p2)}')
    if int((p2.exit_category=='PROTECTIVE_STOP').sum())!=15: errors.append('P2 stop count changed')
    if int((p2.exit_category=='D_REVERSAL').sum())!=11: errors.append('P2 D count changed')
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_SELECTION_DIAGNOSTIC_V9_P2',
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),'frozen_policy':P2,'trades':int(len(p2)),
      'stops':int((p2.exit_category=='PROTECTIVE_STOP').sum()),'d_reversal':int((p2.exit_category=='D_REVERSAL').sum()),
      'early_false_positive':int((p2.exit_category=='EARLY_FALSE_POSITIVE').sum()),
      'feature_comparison':res.to_dict('records'),
      'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,
        'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True,'p2_thresholds_unchanged':True,
        'diagnostic_only_no_rule_fitted':True},
      'decision':'DIAGNOSTIC_ONLY_NEXT_STAGE_MUST_PREDECLARE_SMALL_RULE_FAMILY',
      'validation_errors':errors,
      'limitations':['SAME_BANK_DIAGNOSTIC','SMALL_SAMPLE','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FUNDAMENTAL_PIT_YET','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    res.to_csv(OUT_CSV,index=False); p2.to_csv(OUT_TRADES,index=False)
    lines=['# AT Weekly V9 — P2 stop vs D diagnostic','',f"Status: **{payload['status']}**",f"Frozen P2 trades: **{len(p2)}**; stops: **{payload['stops']}**; D: **{payload['d_reversal']}**; FP: **{payload['early_false_positive']}**",'',
      '| Feature | Stop median | D median | D-Stop | Std median gap |','|---|---:|---:|---:|---:|']
    for r in res.to_dict('records'):
        lines.append(f"| {r['feature']} | {r['stop_median']} | {r['d_median']} | {r['d_minus_stop_median']} | {r['standardized_median_gap']} |")
    lines += ['','This is diagnostic only. No threshold is promoted from these outcomes. A later stage must pre-declare a small candidate family and stress it temporally.']
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'trades':len(p2),'stops':payload['stops'],'d':payload['d_reversal'],'top_features':res.head(5).to_dict('records'),'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__': run()
