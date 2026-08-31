"""Research-only PIT growth-potential diagnostic study.

No historical analyst target is fabricated. CONSENSUS_UPSIDE_PIT_PCT remains null
unless a validated dated source is added later. Technical features are computed only
from completed-week information available at the original signal date.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger, START

ROOT = Path(__file__).resolve().parents[3]
OUTDIR = ROOT / 'outputs/backtest'
OUT_JSON = OUTDIR / 'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V1.json'
OUT_TRADES = OUTDIR / 'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V1_TRADES.csv'
OUT_BUCKETS = OUTDIR / 'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V1_BUCKETS.csv'
OUT_MD = OUTDIR / 'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V1.md'


def clip_score(v, lo, hi, weight):
    if v is None or not np.isfinite(v):
        return 0.0
    if hi <= lo:
        return 0.0
    x = min(max(float(v), lo), hi)
    return weight * (x-lo)/(hi-lo)


def feature_row(b, i):
    c=float(b.close.iloc[i])
    prior52=b.close.shift(1).rolling(52,min_periods=26).max().iloc[i]
    m12=(c/float(b.close.iloc[i-12])-1)*100 if i>=12 and np.isfinite(b.close.iloc[i-12]) else np.nan
    m26=(c/float(b.close.iloc[i-26])-1)*100 if i>=26 and np.isfinite(b.close.iloc[i-26]) else np.nan
    m4=(c/float(b.close.iloc[i-4])-1)*100 if i>=4 and np.isfinite(b.close.iloc[i-4]) else np.nan
    s20=float(b.sma20.iloc[i]) if 'sma20' in b.columns and np.isfinite(b.sma20.iloc[i]) else np.nan
    s50=float(b.sma50.iloc[i]) if 'sma50' in b.columns and np.isfinite(b.sma50.iloc[i]) else np.nan
    d20=(c/s20-1)*100 if np.isfinite(s20) and s20>0 else np.nan
    d50=(c/s50-1)*100 if np.isfinite(s50) and s50>0 else np.nan
    if np.isfinite(prior52) and prior52>0:
        upside=(float(prior52)/c-1)*100
        breakout=max(0.0,(c/float(prior52)-1)*100)
    else:
        upside=np.nan; breakout=np.nan
    rsi=float(b.rsi14.iloc[i]) if 'rsi14' in b.columns and np.isfinite(b.rsi14.iloc[i]) else np.nan
    stoch=float(b.stoch_k.iloc[i]) if 'stoch_k' in b.columns and np.isfinite(b.stoch_k.iloc[i]) else np.nan

    # Fixed ex-ante diagnostic score: no outcome fitting.
    # 25 momentum12 + 20 momentum26 + 15 acceleration4 + 15 breakout +
    # 15 SMA20 trend + 10 SMA50 trend = 100 maximum.
    score=(
        clip_score(m12,-10,30,25)+
        clip_score(m26,-15,50,20)+
        clip_score(m4,-5,15,15)+
        clip_score(breakout,0,10,15)+
        clip_score(d20,-5,15,15)+
        clip_score(d50,-10,25,10)
    )
    return {
        'consensus_upside_pit_pct':None,
        'upside_to_prior_52w_high_pct':None if not np.isfinite(upside) else round(float(upside),3),
        'breakout_above_prior_52w_high_pct':None if not np.isfinite(breakout) else round(float(breakout),3),
        'momentum_12w_pct':None if not np.isfinite(m12) else round(float(m12),3),
        'momentum_26w_pct':None if not np.isfinite(m26) else round(float(m26),3),
        'acceleration_4w_pct':None if not np.isfinite(m4) else round(float(m4),3),
        'distance_sma20_pct':None if not np.isfinite(d20) else round(float(d20),3),
        'distance_sma50_pct':None if not np.isfinite(d50) else round(float(d50),3),
        'rsi14':None if not np.isfinite(rsi) else round(float(rsi),3),
        'stoch_k':None if not np.isfinite(stoch) else round(float(stoch),3),
        'tech_growth_score_pit_v1':round(float(score),3),
    }


def enrich_trades(bars,ledger):
    rows=[]
    for _,r in ledger.iterrows():
        if pd.Timestamp(r['entry_date']) < START:
            continue
        sym=r['symbol']; b=bars.get(sym)
        if b is None or not r.get('signal_date'):
            continue
        sd=pd.Timestamp(r['signal_date'])
        idx=b.index.get_indexer([sd])
        if len(idx)==0 or idx[0]<0:
            continue
        i=int(idx[0])
        z=dict(r)
        z.update(feature_row(b,i))
        z['feature_timestamp']=sd.date().isoformat()
        z['feature_timestamp_le_signal']=True
        rows.append(z)
    return pd.DataFrame(rows)


def rr_pf(df):
    real=df[~df.endpoint_mark.astype(bool)].copy()
    wins=real[real.return_pct>0]; losses=real[real.return_pct<0]
    rr=None; pf=None
    if not wins.empty and not losses.empty and float(losses.return_pct.mean())!=0:
        rr=float(wins.return_pct.mean()/abs(losses.return_pct.mean()))
    loss_sum=-float(losses.return_pct.sum()) if not losses.empty else 0.0
    if loss_sum>0:
        pf=float(wins.return_pct.sum()/loss_sum)
    return real,wins,losses,rr,pf


def summarize_bucket(df,dimension,bucket):
    real,wins,losses,rr,pf=rr_pf(df)
    return {
        'dimension':dimension,'bucket':bucket,'trades':int(len(real)),
        'win_rate_pct':None if real.empty else round(float((real.return_pct>0).mean()*100),2),
        'mean_return_pct':None if real.empty else round(float(real.return_pct.mean()),3),
        'avg_win_pct':None if wins.empty else round(float(wins.return_pct.mean()),3),
        'avg_loss_pct':None if losses.empty else round(float(losses.return_pct.mean()),3),
        'reward_risk':None if rr is None else round(rr,3),
        'profit_factor':None if pf is None else round(pf,3),
        'p10_return_pct':None if real.empty else round(float(real.return_pct.quantile(.10)),3),
        'max_loss_pct':None if losses.empty else round(float(losses.return_pct.min()),3),
        'robust_sample':bool(len(real)>=20),
    }


def bucketize(df):
    rows=[]
    score_bins=[(-np.inf,40,'<40'),(40,55,'40-55'),(55,70,'55-70'),(70,85,'70-85'),(85,np.inf,'>=85')]
    for lo,hi,name in score_bins:
        m=(df.tech_growth_score_pit_v1>=lo)&(df.tech_growth_score_pit_v1<hi)
        rows.append(summarize_bucket(df[m],'tech_growth_score',name))
    up=df.upside_to_prior_52w_high_pct
    specs=[(-np.inf,0,'<=0'),(0,10,'0-10'),(10,20,'10-20'),(20,30,'20-30'),(30,np.inf,'>30')]
    for lo,hi,name in specs:
        m=up.notna()&(up>lo)&(up<=hi) if name=='<=0' else up.notna()&(up>=lo)&(up<hi)
        rows.append(summarize_bucket(df[m],'upside_to_prior_52w_high_pct',name))
    mom=df.momentum_12w_pct
    specs2=[(-np.inf,0,'<0'),(0,10,'0-10'),(10,20,'10-20'),(20,30,'20-30'),(30,np.inf,'>30')]
    for lo,hi,name in specs2:
        m=mom.notna()&(mom>=lo)&(mom<hi)
        rows.append(summarize_bucket(df[m],'momentum_12w_pct',name))
    for q,g in df.groupby(pd.to_datetime(df.exit_date).dt.to_period('Q').astype(str)):
        rows.append(summarize_bucket(g,'exit_quarter',q))
    return pd.DataFrame(rows)


def run():
    bars,arr,sigs,first,last=build_universe()
    ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger)
    if df.empty:
        raise RuntimeError('no PIT-enriched trades')
    buckets=bucketize(df)
    real=df[~df.endpoint_mark.astype(bool)]
    coverage=100.0*real.consensus_upside_pit_pct.notna().mean() if len(real) else 0.0
    errors=[]
    if not bool(df.feature_timestamp_le_signal.all()): errors.append('feature timestamp after signal')
    if coverage!=0.0: errors.append('unexpected historical consensus values in V1')
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    payload={
        'status':'SUCCESS' if not errors else 'VALIDATION_FAILED',
        'version':'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V1',
        'generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'data_window':{'first_week':pd.Timestamp(first).date().isoformat(),'last_week':pd.Timestamp(last).date().isoformat()},
        'realised_trades':int(len(real)),
        'consensus_upside_pit_coverage_pct':round(float(coverage),3),
        'consensus_policy':'NULL_UNLESS_DATE_STAMPED_SOURCE_AVAILABLE_AT_OR_BEFORE_SIGNAL',
        'score_formula':{
            'momentum_12w':'25 points, clipped -10% to +30%',
            'momentum_26w':'20 points, clipped -15% to +50%',
            'acceleration_4w':'15 points, clipped -5% to +15%',
            'breakout_prior_52w':'15 points, clipped 0% to +10%',
            'distance_sma20':'15 points, clipped -5% to +15%',
            'distance_sma50':'10 points, clipped -10% to +25%',
            'outcome_fitting':False,
        },
        'lookahead_controls':{
            'completed_week_features_only':True,
            'prior_52w_uses_shift_1':True,
            'feature_timestamp_le_signal':True,
            'current_consensus_backfill_forbidden':True,
            'locked_trade_ledger_reused':True,
        },
        'bucket_results':buckets.to_dict('records'),
        'validation_errors':errors,
        'limitations':['NO_VALIDATED_HISTORICAL_BOURSORAMA_CONSENSUS','TECH_SCORE_IS_PROXY_NOT_ANALYST_TARGET','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','RESEARCH_ONLY'],
    }
    OUTDIR.mkdir(parents=True,exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    df.to_csv(OUT_TRADES,index=False)
    buckets.to_csv(OUT_BUCKETS,index=False)
    lines=['# AT Weekly Growth Potential PIT V1','',f"Status: **{payload['status']}**",f"Historical consensus coverage: **{payload['consensus_upside_pit_coverage_pct']}%**",'',
           'No current analyst target was backfilled into history. TECH_GROWTH_SCORE_PIT_V1 is a technical diagnostic proxy, not an analyst consensus target.','',
           '| Dimension | Bucket | Trades | Win % | Mean % | Avg win % | Avg loss % | RR | PF | P10 % | Max loss % | Robust sample |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in buckets.to_dict('records'):
        lines.append(f"| {r['dimension']} | {r['bucket']} | {r['trades']} | {r['win_rate_pct']} | {r['mean_return_pct']} | {r['avg_win_pct']} | {r['avg_loss_pct']} | {r['reward_risk']} | {r['profit_factor']} | {r['p10_return_pct']} | {r['max_loss_pct']} | {r['robust_sample']} |")
    OUT_MD.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'realised_trades':payload['realised_trades'],'consensus_coverage_pct':coverage,'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__':
    run()
