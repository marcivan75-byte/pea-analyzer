"""Research-only V4: freeze MOM12>=40 and stress it temporally.

The threshold is NOT re-optimized here. It was selected from prior V2/V3 evidence,
therefore this is a frozen-candidate temporal stress test, not clean OOS.
Locked entry/exit logic and PIT feature construction are unchanged.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import pandas as pd

from .at_weekly_growth_potential_pit_v1 import enrich_trades, summarize_bucket
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V4_FROZEN_MOM40.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V4_FROZEN_MOM40.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V4_FROZEN_MOM40.md'
THRESHOLD=40.0


def row_metrics(df,label,period):
    z=summarize_bucket(df,label,period)
    z['period']=period
    return z


def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger)
    if df.empty: raise RuntimeError('no PIT-enriched trades')
    df=df[~df.endpoint_mark.astype(bool)].copy()
    df['exit_date']=pd.to_datetime(df.exit_date)
    gate=(df.momentum_12w_pct>=THRESHOLD).fillna(False)

    periods={
      'ALL': pd.Series(True,index=df.index),
      '2024': df.exit_date.dt.year.eq(2024),
      '2025': df.exit_date.dt.year.eq(2025),
      '2026_YTD': df.exit_date.dt.year.eq(2026),
      'LATE_FROM_2025H2': df.exit_date.ge(pd.Timestamp('2025-07-01')),
    }
    rows=[]; stress=[]
    for period,mask in periods.items():
        b=df[mask]; g=df[mask & gate]
        rb=row_metrics(b,'BASELINE',period); rg=row_metrics(g,'MOM12_GE40',period)
        rb['coverage_pct']=100.0; rg['coverage_pct']=round(100*len(g)/len(b),2) if len(b) else 0.0
        rows += [rb,rg]
        stress.append({
          'period':period,'baseline_trades':int(len(b)),'filtered_trades':int(len(g)),'coverage_pct':rg['coverage_pct'],
          'baseline_mean_pct':rb['mean_return_pct'],'filtered_mean_pct':rg['mean_return_pct'],
          'delta_mean_pct':None if rb['mean_return_pct'] is None or rg['mean_return_pct'] is None else round(rg['mean_return_pct']-rb['mean_return_pct'],3),
          'baseline_pf':rb['profit_factor'],'filtered_pf':rg['profit_factor'],
          'delta_pf':None if rb['profit_factor'] is None or rg['profit_factor'] is None else round(rg['profit_factor']-rb['profit_factor'],3),
          'baseline_rr':rb['reward_risk'],'filtered_rr':rg['reward_risk'],
          'delta_rr':None if rb['reward_risk'] is None or rg['reward_risk'] is None else round(rg['reward_risk']-rb['reward_risk'],3),
          'sample_guard':bool(len(g)>=20),
          'direction_guard':bool(rg['mean_return_pct'] is not None and rg['mean_return_pct']>0),
        })
    s=pd.DataFrame(stress)
    evaluable=s[s.sample_guard]
    improved_mean=int((evaluable.delta_mean_pct>0).sum())
    improved_pf=int((evaluable.delta_pf>0).sum())
    positive=int(evaluable.direction_guard.sum())
    errors=[]
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    if not bool(df.feature_timestamp_le_signal.all()): errors.append('feature timestamp after signal')
    payload={
      'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V4_FROZEN_MOM40',
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),'realised_trades':int(len(df)),
      'frozen_candidate':{'feature':'momentum_12w_pct','threshold':THRESHOLD,'operator':'>='},
      'hypothesis_status':'FROZEN_AFTER_V3_BUT_NOT_CLEAN_OOS',
      'temporal_stress':s.to_dict('records'),
      'evaluable_periods_n':int(len(evaluable)),'positive_periods_n':positive,'mean_improved_periods_n':improved_mean,'pf_improved_periods_n':improved_pf,
      'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True,'threshold_reoptimized_in_v4':False},
      'validation_errors':errors,
      'limitations':['CANDIDATE_SELECTED_USING_PRIOR_SAME_BANK_RESULTS','NOT_CLEAN_OOS','NO_HISTORICAL_ANALYST_CONSENSUS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    pd.DataFrame(rows).to_csv(OUT_CSV,index=False)
    lines=['# AT Weekly Growth Potential PIT V4 Frozen MOM40','',f"Status: **{payload['status']}**",'',
      'Frozen candidate: **12-week momentum >= 40%**. No threshold optimization in V4. This remains a temporal stress test, not clean OOS.','',
      '| Period | Base n | Filter n | Coverage % | Base mean % | Filter mean % | Delta mean | Base PF | Filter PF | Base RR | Filter RR | Sample guard |',
      '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in stress:
        lines.append(f"| {r['period']} | {r['baseline_trades']} | {r['filtered_trades']} | {r['coverage_pct']} | {r['baseline_mean_pct']} | {r['filtered_mean_pct']} | {r['delta_mean_pct']} | {r['baseline_pf']} | {r['filtered_pf']} | {r['baseline_rr']} | {r['filtered_rr']} | {r['sample_guard']} |")
    OUT_MD.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'realised_trades':payload['realised_trades'],'evaluable_periods_n':payload['evaluable_periods_n'],'mean_improved_periods_n':improved_mean,'pf_improved_periods_n':improved_pf,'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__': run()
