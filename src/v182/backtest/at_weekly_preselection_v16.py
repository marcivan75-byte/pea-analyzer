"""Research-only V16: coarse AT preselection on existing PIT technical features only.

Priority axes are deliberately limited to two already available variables:
1) 4-week acceleration; 2) breakout above prior 52-week high.
No new data collection, no exit changes, no outcome-fitted continuous optimization.
Portfolio contract is inherited from V14: EUR60k, EUR4k/title, max 12 live,
30 entries/year, 0.20% each side, max DD 12%.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_selection_quality_v8_top30 import potential_score
from .at_weekly_growth_potential_pit_v1 import enrich_trades
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger
from .at_weekly_capacity_quality_v13 import (
    portfolio_sim, trade_metrics, ANNUAL_CAP, MAX_LIVE_POSITIONS,
    FULL_NOMINAL_EUR, INITIAL_CAPITAL, MAX_DRAWDOWN_PCT, COMPLETE_YEARS,
)
from .at_weekly_staged_entry_v11 import load_daily
from .at_weekly_ranked_capacity_v14 import rank_score

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_PRESELECTION_V16.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_PRESELECTION_V16.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_PRESELECTION_V16_TRADES.csv'
OUT_EQUITY=OUTDIR/'AT_WEEKLY_PRESELECTION_V16_EQUITY.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_PRESELECTION_V16.md'

# Coarse, pre-declared thresholds only. Same ranking/capacity mechanics for every variant.
CANDIDATES=[]
for a in (30.0,40.0,50.0,60.0):
    CANDIDATES.append({'name':f'A4_{int(a)}','mode':'A4','a4':a})
for b in (10.0,20.0,30.0,40.0):
    CANDIDATES.append({'name':f'BO_{int(b)}','mode':'BO','bo':b})
for a in (30.0,40.0,50.0):
    for b in (10.0,20.0,30.0):
        CANDIDATES.append({'name':f'A4_{int(a)}_BO_{int(b)}','mode':'AND','a4':a,'bo':b})
CANDIDATES += [
    {'name':'A4_40_OR_BO_30','mode':'OR','a4':40.0,'bo':30.0},
    {'name':'A4_50_OR_BO_30','mode':'OR','a4':50.0,'bo':30.0},
]

COOLDOWN_WEEKS=4
WEEKLY_CAP=3

def apply_gate(z,c):
    a=pd.to_numeric(z['acceleration_4w_pct'],errors='coerce')
    b=pd.to_numeric(z['breakout_above_prior_52w_high_pct'],errors='coerce')
    if c['mode']=='A4': return z[a>=c['a4']].copy()
    if c['mode']=='BO': return z[b>=c['bo']].copy()
    if c['mode']=='AND': return z[(a>=c['a4']) & (b>=c['bo'])].copy()
    if c['mode']=='OR': return z[(a>=c['a4']) | (b>=c['bo'])].copy()
    raise ValueError(c)

def select_candidate(df,c):
    z=apply_gate(df,c)
    z['signal_date']=pd.to_datetime(z.signal_date)
    z['quality_score_v8']=z.apply(potential_score,axis=1)
    z['rank_score_v16']=z.apply(lambda r:rank_score(r,'QM'),axis=1)
    z=z.sort_values(['signal_date','symbol','rank_score_v16'],ascending=[True,True,False]).drop_duplicates(['signal_date','symbol'])
    selected=[]; annual={}; last_sym={}
    for dt,g in z.groupby('signal_date',sort=True):
        y=int(pd.Timestamp(dt).year); used=annual.get(y,0)
        if used>=ANNUAL_CAP: continue
        cand=[]
        for _,r in g.sort_values(['rank_score_v16','quality_score_v8','momentum_26w_pct'],ascending=False).iterrows():
            prev=last_sym.get(r.symbol)
            if prev is not None and (pd.Timestamp(dt)-prev).days<7*COOLDOWN_WEEKS: continue
            cand.append(r)
        if not cand: continue
        pick=pd.DataFrame(cand).head(min(WEEKLY_CAP,ANNUAL_CAP-used))
        if pick.empty: continue
        selected.append(pick); annual[y]=used+len(pick)
        for _,r in pick.iterrows(): last_sym[r.symbol]=pd.Timestamp(dt)
    return pd.concat(selected,ignore_index=True) if selected else z.iloc[0:0].copy()

def run():
    bars,arr,sigs,first,last=build_universe()
    ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger)
    df=df[~df.endpoint_mark.astype(bool)].copy()
    errors=[]
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    daily=load_daily(); rows=[]; portfolios={}; audits=[]; equities=[]
    for c in CANDIDATES:
        s=select_candidate(df,c)
        tm=trade_metrics(s); ps,eq,audit=portfolio_sim(s,daily)
        annual_counts={int(y):int(n) for y,n in s.groupby(pd.to_datetime(s.signal_date).dt.year).size().items()} if not s.empty else {}
        full=[x for x in ps.get('annual',[]) if x.get('period_type')=='FULL_YEAR']
        strict_econ=bool(len(full)==len(COMPLETE_YEARS) and all(x['net_return_pct']>15.0 for x in full))
        rr_ok=bool(tm['rr'] is not None and tm['rr']>3.3)
        pf_ok=bool(tm['pf'] is not None and tm['pf']>=2.0)
        dd_ok=bool(ps.get('max_drawdown_pct') is not None and ps['max_drawdown_pct']>=-MAX_DRAWDOWN_PCT)
        cap_ok=bool(ps.get('max_live_positions_seen',99)<=MAX_LIVE_POSITIONS and all(v<=ANNUAL_CAP for v in annual_counts.values()))
        annual_net={str(x['year']):x['net_return_pct'] for x in ps.get('annual',[])}
        row={'candidate':c['name'],'mode':c['mode'],'a4_min':c.get('a4'),'bo_min':c.get('bo'),**tm,
             'annual_entry_counts':annual_counts,'annual_net_return_pct':annual_net,
             'complete_year_mean_net_pct':ps.get('complete_year_mean_net_pct'),'complete_year_min_net_pct':ps.get('complete_year_min_net_pct'),
             'max_drawdown_pct':ps.get('max_drawdown_pct'),'max_live_positions_seen':ps.get('max_live_positions_seen'),
             'cumulative_net_return_pct':ps.get('cumulative_net_return_pct'),'strict_each_complete_year_gt15':strict_econ,
             'rr_gt_3_3':rr_ok,'pf_ge_2':pf_ok,'drawdown_le_12':dd_ok,'promotion_candidate':bool(strict_econ and rr_ok and pf_ok and dd_ok and cap_ok)}
        rows.append(row); portfolios[c['name']]=ps
        if not audit.empty: audit=audit.copy(); audit['candidate']=c['name']; audits.append(audit)
        if not eq.empty: eq=eq.copy(); eq['candidate']=c['name']; equities.append(eq)
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_PRESELECTION_V16','generated_at_utc':datetime.now(timezone.utc).isoformat(),
             'objective':'TEST_FEW_EXISTING_AT_PRESELECTION_AXES_WITH_COARSE_THRESHOLDS',
             'portfolio_contract':{'initial_capital_eur':INITIAL_CAPITAL,'full_nominal_per_title_eur':FULL_NOMINAL_EUR,'max_live_positions':MAX_LIVE_POSITIONS,'annual_entry_cap':ANNUAL_CAP,'max_drawdown_pct':MAX_DRAWDOWN_PCT,'weekly_cap':WEEKLY_CAP,'cooldown_weeks':COOLDOWN_WEEKS},
             'promotion_gates':{'each_complete_year_net_gt_pct':15.0,'rr_gt':3.3,'pf_ge':2.0,'max_drawdown_abs_le_pct':12.0},
             'features_used':['acceleration_4w_pct','breakout_above_prior_52w_high_pct','existing_QM_rank_only_for_capacity_ordering'],
             'candidates':CANDIDATES,'results':rows,'portfolio_results':portfolios,
             'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,'future_signals_not_used_for_current_ranking':True,'outcome_not_used_in_candidate_score':True,'coarse_thresholds_predeclared':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True,'no_new_data_collection':True},
             'validation_errors':sorted(set(errors)),
             'limitations':['SAME_BANK_RESEARCH_NOT_CLEAN_OOS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','2026_IS_YTD','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    pd.DataFrame(rows).to_csv(OUT_CSV,index=False)
    (pd.concat(audits,ignore_index=True) if audits else pd.DataFrame()).to_csv(OUT_TRADES,index=False)
    (pd.concat(equities,ignore_index=True) if equities else pd.DataFrame()).to_csv(OUT_EQUITY,index=False)
    lines=['# AT Weekly V16 — Priority preselection matrix','',f"Status: **{payload['status']}**",'',
           'Only existing PIT technical features are used: 4-week acceleration and 52-week breakout.','',
           '| Candidate | Trades | RR | PF | Mean % | 2024 net % | 2025 net % | 2026 YTD % | Max DD % | Promotion |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        a=r['annual_net_return_pct']
        lines.append(f"| {r['candidate']} | {r['trades']} | {r['rr']} | {r['pf']} | {r['mean_trade_return_pct']} | {a.get('2024')} | {a.get('2025')} | {a.get('2026')} | {r['max_drawdown_pct']} | {r['promotion_candidate']} |")
    lines += ['','2026 is YTD. No candidate is promoted from this same-bank research alone.']
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'results':rows,'validation_errors':payload['validation_errors']},indent=2,ensure_ascii=False))
    return payload

if __name__=='__main__': run()
