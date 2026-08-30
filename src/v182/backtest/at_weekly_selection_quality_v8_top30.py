"""Research-only V8: harden Top30 admission quality without lookahead.

Goal: keep <=30 executed entries/year while rejecting mediocre/overextended setups and
favoring high ex-ante potential. Locked entry/exit architecture is unchanged. This is
same-bank exploratory research, not clean OOS.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, numpy as np, pandas as pd

from .at_weekly_selection_capacity_v7_top30 import potential_score, metrics
from .at_weekly_growth_potential_pit_v1 import enrich_trades
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_SELECTION_QUALITY_V8_TOP30.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_SELECTION_QUALITY_V8_TOP30.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_SELECTION_QUALITY_V8_TOP30_TRADES.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_SELECTION_QUALITY_V8_TOP30.md'
ANNUAL_CAP=30; WEEKLY_CAP=1

POLICIES=[
 {'name':'P0_MOM40','score_min':0,'cooldown_weeks':0},
 {'name':'P1_SCORE85_CD8','score_min':85,'cooldown_weeks':8},
 {'name':'P2_SCORE88_CD12','score_min':88,'cooldown_weeks':12},
 {'name':'P3_BALANCED85_CD12','score_min':85,'cooldown_weeks':12,'m26_min':30,'rsi_max':75,'stoch_max':90,'accel_min':-2,'accel_max':25,'d20_min':0,'d20_max':20,'d50_min':0,'d50_max':40},
 {'name':'P4_BALANCED88_CD12','score_min':88,'cooldown_weeks':12,'m26_min':30,'rsi_max':75,'stoch_max':90,'accel_min':-2,'accel_max':25,'d20_min':0,'d20_max':20,'d50_min':0,'d50_max':40},
]

def finite_ge(r,k,v):
    x=r.get(k,np.nan); return bool(np.isfinite(x) and float(x)>=v)
def finite_le(r,k,v):
    x=r.get(k,np.nan); return bool(np.isfinite(x) and float(x)<=v)

def passes(r,p):
    if not finite_ge(r,'momentum_12w_pct',40.0): return False
    if float(r['quality_score_v8']) < p['score_min']: return False
    checks=[('momentum_26w_pct','m26_min','ge'),('rsi14','rsi_max','le'),('stoch_k','stoch_max','le'),
            ('acceleration_4w_pct','accel_min','ge'),('acceleration_4w_pct','accel_max','le'),
            ('distance_sma20_pct','d20_min','ge'),('distance_sma20_pct','d20_max','le'),
            ('distance_sma50_pct','d50_min','ge'),('distance_sma50_pct','d50_max','le')]
    for col,key,op in checks:
        if key in p:
            if op=='ge' and not finite_ge(r,col,p[key]): return False
            if op=='le' and not finite_le(r,col,p[key]): return False
    return True

def select(df,p):
    z=df.copy(); z['signal_date']=pd.to_datetime(z.signal_date)
    z['quality_score_v8']=z.apply(potential_score,axis=1)
    # Compatibility alias only: V7 metrics expects this column name. Values are
    # exactly the V8 ex-ante quality score; no criterion, threshold or ranking logic
    # is changed by this technical fix.
    z['potential_score_v7']=z['quality_score_v8']
    z=z[z.apply(lambda r: passes(r,p),axis=1)].copy()
    z=z.sort_values(['signal_date','symbol','quality_score_v8'],ascending=[True,True,False]).drop_duplicates(['signal_date','symbol'])
    selected=[]; annual={}; last_sym={}
    for dt,g in z.groupby('signal_date',sort=True):
        year=int(pd.Timestamp(dt).year); used=annual.get(year,0)
        if used>=ANNUAL_CAP: continue
        cand=[]
        for _,r in g.sort_values(['quality_score_v8','momentum_12w_pct','momentum_26w_pct'],ascending=False).iterrows():
            prev=last_sym.get(r.symbol)
            if prev is not None and (pd.Timestamp(dt)-prev).days < 7*int(p['cooldown_weeks']):
                continue
            cand.append(r)
        if not cand: continue
        pick=pd.DataFrame(cand).head(min(WEEKLY_CAP,ANNUAL_CAP-used))
        if pick.empty: continue
        selected.append(pick); annual[year]=used+len(pick)
        for _,r in pick.iterrows(): last_sym[r.symbol]=pd.Timestamp(dt)
    if not selected: return z.iloc[0:0].copy()
    return pd.concat(selected,ignore_index=True)

def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    if df.empty: raise RuntimeError('no realised trades')
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): raise RuntimeError('lookahead feature timestamp')
    years=sorted(pd.to_datetime(df.signal_date).dt.year.unique())
    rows=[]; frames=[]
    for p in POLICIES:
        s=select(df,p); s['policy']=p['name']; frames.append(s)
        rows.append({'policy':p['name'],'period':'ALL',**metrics(s)})
        for y in years:
            sy=s[pd.to_datetime(s.signal_date).dt.year.eq(y)]
            rows.append({'policy':p['name'],'period':str(y),**metrics(sy)})
    res=pd.DataFrame(rows)
    base=res[(res.policy=='P0_MOM40')&(res.period=='ALL')].iloc[0]
    decisions=[]
    for p in POLICIES[1:]:
        allr=res[(res.policy==p['name'])&(res.period=='ALL')].iloc[0]
        yr=res[(res.policy==p['name'])&(res.period!='ALL')]
        evaluable=yr[yr.trades>=8]
        robust=bool(allr.trades>=25 and len(evaluable)>=2 and (evaluable.mean_return_pct>0).all() and (evaluable.profit_factor>1).all())
        rr_improve=bool(pd.notna(allr.reward_risk) and pd.notna(base.reward_risk) and allr.reward_risk>base.reward_risk)
        stop_rate=(allr.stop_count/allr.trades) if allr.trades else np.nan
        base_stop=(base.stop_count/base.trades) if base.trades else np.nan
        tp_rate=(allr.tp_d_count/allr.trades) if allr.trades else np.nan
        base_tp=(base.tp_d_count/base.trades) if base.trades else np.nan
        quality_gain=bool(np.isfinite(stop_rate) and stop_rate<base_stop and np.isfinite(tp_rate) and tp_rate>base_tp)
        decisions.append({'policy':p['name'],'robust_guard':robust,'rr_improves':rr_improve,'stop_and_tp_improve':quality_gain,
                          'candidate_retain':bool(robust and rr_improve and quality_gain)})
    dec=pd.DataFrame(decisions)
    retained=dec[dec.candidate_retain]
    best=None
    if not retained.empty:
        names=retained.policy.tolist(); q=res[(res.period=='ALL')&res.policy.isin(names)].copy()
        q=q.sort_values(['reward_risk','profit_factor','mean_return_pct'],ascending=False)
        best=q.iloc[0].to_dict()
    errors=[]
    for s in frames:
        if not s.empty:
            if (s.groupby(pd.to_datetime(s.signal_date).dt.year).size()>ANNUAL_CAP).any(): errors.append('annual cap exceeded')
            if (s.groupby(pd.to_datetime(s.signal_date)).size()>WEEKLY_CAP).any(): errors.append('weekly cap exceeded')
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_SELECTION_QUALITY_V8_TOP30',
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),'policies':POLICIES,'results':res.to_dict('records'),'decisions':decisions,
      'best_guarded_candidate':best,'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,
      'future_signals_not_used_for_current_ranking':True,'outcome_not_used_in_score_or_rejection':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True},
      'validation_errors':errors,'limitations':['SAME_BANK_EXPLORATORY_NOT_CLEAN_OOS','NO_HISTORICAL_ANALYST_CONSENSUS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    res.to_csv(OUT_CSV,index=False); pd.concat(frames,ignore_index=True).to_csv(OUT_TRADES,index=False)
    lines=['# AT Weekly Selection Quality V8 — Top30','',f"Status: **{payload['status']}**",'',
      '| Policy | Period | Trades | Win % | Mean % | RR | PF | TP D % | Stops | Early FP |',
      '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in res.to_dict('records'):
        lines.append(f"| {r['policy']} | {r['period']} | {r['trades']} | {r['win_rate_pct']} | {r['mean_return_pct']} | {r['reward_risk']} | {r['profit_factor']} | {r['tp_d_rate_pct']} | {r['stop_count']} | {r['false_positive_count']} |")
    lines += ['', 'Decision guards:', json.dumps(decisions,ensure_ascii=False), '', f"Best guarded candidate: **{None if best is None else best['policy']}**"]
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'best_guarded_candidate':best,'decisions':decisions,'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__': run()
