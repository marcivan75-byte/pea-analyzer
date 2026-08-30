"""Research-only PIT V6: stress the frozen MOM40 + constructive-regime gate.

V6 does not tune thresholds. It imports the V5 fixed definitions and evaluates the
combined gate over fixed half-year segments plus the full sample. This is robustness
stress, not clean OOS.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import pandas as pd

from .at_weekly_growth_potential_pit_v1 import enrich_trades, summarize_bucket
from .at_weekly_growth_potential_pit_v5_regime import regime_at, MOM12_MIN
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V6_REGIME_STRESS.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V6_REGIME_STRESS.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V6_REGIME_STRESS.md'


def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    if df.empty: raise RuntimeError('no realised PIT trades')
    dates=sorted(pd.to_datetime(df.signal_date).dropna().unique())
    cache={pd.Timestamp(d):regime_at(bars,pd.Timestamp(d)) for d in dates}
    vals=[cache[pd.Timestamp(d)] for d in pd.to_datetime(df.signal_date)]
    df['regime_constructive']=[x[2] for x in vals]
    gate=(df.momentum_12w_pct>=MOM12_MIN)&df.regime_constructive
    xd=pd.to_datetime(df.exit_date)
    periods={
      'ALL':pd.Series(True,index=df.index),
      '2024H1':(xd>=pd.Timestamp('2024-01-01'))&(xd<pd.Timestamp('2024-07-01')),
      '2024H2':(xd>=pd.Timestamp('2024-07-01'))&(xd<pd.Timestamp('2025-01-01')),
      '2025H1':(xd>=pd.Timestamp('2025-01-01'))&(xd<pd.Timestamp('2025-07-01')),
      '2025H2':(xd>=pd.Timestamp('2025-07-01'))&(xd<pd.Timestamp('2026-01-01')),
      '2026H1':(xd>=pd.Timestamp('2026-01-01'))&(xd<pd.Timestamp('2026-07-01')),
      '2026H2_YTD':xd>=pd.Timestamp('2026-07-01'),
    }
    rows=[]
    for p,pm in periods.items():
        for name,g in [('BASELINE',pd.Series(True,index=df.index)),('FROZEN_MOM40_REGIME',gate)]:
            z=summarize_bucket(df[pm & g],name,p); z['candidate']=name; z['period']=p
            z['coverage_pct']=round(100*int((pm&g).sum())/max(1,int(pm.sum())),2)
            rows.append(z)
    res=pd.DataFrame(rows)
    base=res[res.candidate=='BASELINE'].set_index('period'); filt=res[res.candidate=='FROZEN_MOM40_REGIME'].set_index('period')
    robust=[]; improved=[]
    for p in periods:
        if p=='ALL': continue
        r=filt.loc[p]; b=base.loc[p]
        if int(r.trades)>=15:
            robust.append(p)
            if pd.notna(r.mean_return_pct) and pd.notna(b.mean_return_pct) and pd.notna(r.profit_factor) and pd.notna(b.profit_factor) and r.mean_return_pct>b.mean_return_pct and r.profit_factor>b.profit_factor:
                improved.append(p)
    allr=filt.loc['ALL']; allb=base.loc['ALL']
    stable=bool(int(allr.trades)>=50 and allr.mean_return_pct>allb.mean_return_pct and allr.profit_factor>allb.profit_factor and len(robust)>=3 and len(improved)>=max(2,len(robust)-1))
    errors=[]
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    if not bool(df.feature_timestamp_le_signal.all()): errors.append('feature timestamp after signal')
    payload={
      'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V6_REGIME_STRESS',
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),'realised_trades':int(len(df)),
      'hypothesis_status':'FROZEN_V5_GATE_HALF_YEAR_STRESS_NOT_CLEAN_OOS',
      'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,'regime_uses_only_bars_le_signal':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True,'threshold_optimization':False},
      'rows':res.to_dict('records'),'robust_halfyears':robust,'improved_halfyears':improved,
      'stable_candidate':stable,'next_stage':'STOP_LOCK_RESEARCH_CANDIDATE' if stable else 'STOP_REJECT_REGIME_GATE',
      'validation_errors':errors,
      'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NOT_CLEAN_OOS','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    res.to_csv(OUT_CSV,index=False)
    lines=['# AT Weekly Growth Potential PIT V6 Regime Stress','',f"Status: **{payload['status']}**",f"Stable candidate: **{stable}**",f"Next: **{payload['next_stage']}**",'',
      '| Candidate | Period | n | Coverage % | Mean % | PF | RR |','|---|---|---:|---:|---:|---:|---:|']
    for r in res.to_dict('records'):
        lines.append(f"| {r['candidate']} | {r['period']} | {r['trades']} | {r['coverage_pct']} | {r['mean_return_pct']} | {r['profit_factor']} | {r['reward_risk']} |")
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'stable_candidate':stable,'next_stage':payload['next_stage'],'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__': run()
