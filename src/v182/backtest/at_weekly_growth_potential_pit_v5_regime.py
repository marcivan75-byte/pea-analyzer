"""Research-only PIT V5: fixed MOM12>=40 combined with an ex-ante cross-sectional regime.

No entry/exit model is changed. The regime is computed only from completed weekly bars
available at each signal date across the cached action universe. Thresholds are fixed
ex ante (breadth above weekly SMA20 >=55%, median 12w momentum >=0%) and are not fitted
to outcomes. This is still same-bank robustness research, not clean OOS.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_growth_potential_pit_v1 import enrich_trades, summarize_bucket
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V5_REGIME.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V5_REGIME.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V5_REGIME.md'

BREADTH20_MIN=55.0
MEDIAN_MOM12_MIN=0.0
MOM12_MIN=40.0


def regime_at(bars, dt):
    above=[]; moms=[]
    ts=pd.Timestamp(dt)
    for b in bars.values():
        h=b.loc[b.index<=ts]
        if len(h)<20: continue
        r=h.iloc[-1]
        c=float(r['close'])
        s20=float(r['sma20']) if pd.notna(r['sma20']) else np.nan
        if np.isfinite(c) and np.isfinite(s20): above.append(c>s20)
        if len(h)>=13:
            p=float(h.iloc[-13]['close'])
            if np.isfinite(c) and np.isfinite(p) and p>0: moms.append((c/p-1.0)*100.0)
    breadth=np.nan if not above else 100.0*float(np.mean(above))
    med=np.nan if not moms else float(np.median(moms))
    constructive=bool(np.isfinite(breadth) and np.isfinite(med) and breadth>=BREADTH20_MIN and med>=MEDIAN_MOM12_MIN)
    return breadth,med,constructive,len(above)


def m(df,label):
    z=summarize_bucket(df,label,'ALL'); z['candidate']=label
    return z


def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger)
    df=df[~df.endpoint_mark.astype(bool)].copy()
    if df.empty: raise RuntimeError('no realised PIT trades')
    dates=sorted(pd.to_datetime(df.signal_date).dropna().unique())
    cache={pd.Timestamp(d):regime_at(bars,pd.Timestamp(d)) for d in dates}
    vals=[cache[pd.Timestamp(d)] for d in pd.to_datetime(df.signal_date)]
    df['regime_breadth_sma20_pct']=[x[0] for x in vals]
    df['regime_median_mom12_pct']=[x[1] for x in vals]
    df['regime_constructive']=[x[2] for x in vals]
    df['regime_universe_n']=[x[3] for x in vals]
    mom=df.momentum_12w_pct>=MOM12_MIN
    comb=mom & df.regime_constructive
    candidates={'BASELINE':pd.Series(True,index=df.index),'MOM40':mom,'REGIME_ONLY':df.regime_constructive,'MOM40_REGIME':comb}
    periods={
      'ALL':pd.Series(True,index=df.index),
      '2024':pd.to_datetime(df.exit_date).dt.year.eq(2024),
      '2025':pd.to_datetime(df.exit_date).dt.year.eq(2025),
      '2026_YTD':pd.to_datetime(df.exit_date).dt.year.eq(2026),
      'LATE_FROM_2025H2':pd.to_datetime(df.exit_date).ge(pd.Timestamp('2025-07-01')),
    }
    rows=[]
    for name,g in candidates.items():
        for p,pm in periods.items():
            z=summarize_bucket(df[g & pm],name,p); z['candidate']=name; z['period']=p
            z['coverage_pct']=round(100*int((g&pm).sum())/max(1,int(pm.sum())),2)
            rows.append(z)
    res=pd.DataFrame(rows)
    base=res[res.candidate=='BASELINE'].set_index('period'); combo=res[res.candidate=='MOM40_REGIME'].set_index('period')
    evaluable=[]; improved=[]
    for p in ['2025','2026_YTD','LATE_FROM_2025H2']:
        r=combo.loc[p]; b=base.loc[p]
        if int(r.trades)>=20:
            evaluable.append(p)
            if pd.notna(r.mean_return_pct) and pd.notna(b.mean_return_pct) and pd.notna(r.profit_factor) and pd.notna(b.profit_factor) and r.mean_return_pct>b.mean_return_pct and r.profit_factor>b.profit_factor:
                improved.append(p)
    allc=combo.loc['ALL']; allb=base.loc['ALL']
    gate=bool(int(allc.trades)>=50 and pd.notna(allc.mean_return_pct) and pd.notna(allc.profit_factor) and allc.mean_return_pct>allb.mean_return_pct and allc.profit_factor>allb.profit_factor and len(improved)>=2)
    errors=[]
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    if not bool(df.feature_timestamp_le_signal.all()): errors.append('feature timestamp after signal')
    if (df.regime_universe_n<20).any(): errors.append('insufficient cross-sectional regime universe')
    payload={
      'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V5_REGIME',
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),'realised_trades':int(len(df)),
      'fixed_rules':{'momentum_12w_min_pct':MOM12_MIN,'breadth_above_weekly_sma20_min_pct':BREADTH20_MIN,'cross_section_median_mom12_min_pct':MEDIAN_MOM12_MIN},
      'hypothesis_status':'EX_ANTE_REGIME_THRESHOLDS_SAME_BANK_ROBUSTNESS_NOT_CLEAN_OOS',
      'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,'regime_uses_only_bars_le_signal':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True,'threshold_optimization':False},
      'rows':res.to_dict('records'),'evaluable_periods':evaluable,'improved_periods':improved,
      'continuation_gate':gate,'next_stage':'v6_regime_stress' if gate else 'STOP_REGIME_NOT_MATERIAL',
      'validation_errors':errors,
      'limitations':['CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','REGIME_IS_INTERNAL_CROSS_SECTION_PROXY_NOT_EXTERNAL_INDEX','NOT_CLEAN_OOS','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    res.to_csv(OUT_CSV,index=False)
    lines=['# AT Weekly Growth Potential PIT V5 Regime','',f"Status: **{payload['status']}**",'',f"Continuation gate: **{gate}** — next: **{payload['next_stage']}**",'',
      '| Candidate | Period | n | Coverage % | Mean % | PF | RR | P10 % |','|---|---|---:|---:|---:|---:|---:|---:|']
    for r in res.to_dict('records'):
        lines.append(f"| {r['candidate']} | {r['period']} | {r['trades']} | {r['coverage_pct']} | {r['mean_return_pct']} | {r['profit_factor']} | {r['reward_risk']} | {r['p10_return_pct']} |")
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'realised_trades':len(df),'continuation_gate':gate,'next_stage':payload['next_stage'],'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__': run()
