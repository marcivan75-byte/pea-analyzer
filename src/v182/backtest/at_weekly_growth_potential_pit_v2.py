"""Research-only PIT growth-potential V2: non-saturating ex-ante technical score.

V1 saturated because several positively-trending features clipped at low ceilings. V2
keeps the locked trade ledger and all no-lookahead rules, but replaces the score with
fixed, wider, piecewise bands chosen from market-scale feature magnitudes, not trade
outcomes. No historical analyst target is fabricated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_growth_potential_pit_v1 import enrich_trades, rr_pf, summarize_bucket
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V2.json'
OUT_TRADES=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V2_TRADES.csv'
OUT_BUCKETS=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V2_BUCKETS.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V2.md'


def points(v, bands, pts):
    if v is None or not np.isfinite(v): return 0.0
    x=float(v)
    for hi,p in zip(bands,pts):
        if x < hi: return float(p)
    return float(pts[-1])


def score_row(r):
    # Fixed ex-ante V2 bands. Wider ceilings avoid the V1 saturation.
    # Momentum dominates, but extreme extension is not rewarded indefinitely.
    m12=points(r.momentum_12w_pct,[10,20,30,50,80,np.inf],[0,5,10,17,22,25])
    m26=points(r.momentum_26w_pct,[15,30,50,80,120,np.inf],[0,4,9,14,18,20])
    m4=points(r.acceleration_4w_pct,[0,5,10,20,30,np.inf],[0,3,7,11,14,15])
    br=points(r.breakout_above_prior_52w_high_pct,[0.001,3,8,15,30,np.inf],[0,3,7,11,14,15])
    d20=points(r.distance_sma20_pct,[0,5,10,20,35,np.inf],[0,3,7,11,14,12])
    d50=points(r.distance_sma50_pct,[0,10,20,35,60,np.inf],[0,2,5,8,10,8])
    return round(m12+m26+m4+br+d20+d50,3)


def bucketize(df):
    rows=[]
    bins=[(-np.inf,35,'<35'),(35,50,'35-50'),(50,65,'50-65'),(65,80,'65-80'),(80,np.inf,'>=80')]
    for lo,hi,name in bins:
        m=(df.tech_growth_score_pit_v2>=lo)&(df.tech_growth_score_pit_v2<hi)
        rows.append(summarize_bucket(df[m],'tech_growth_score_v2',name))
    # Explicit entry-filter diagnostics, minimum sample shown rather than hidden.
    for th in [40,50,60,70,80]:
        g=df[df.tech_growth_score_pit_v2>=th]
        z=summarize_bucket(g,'score_filter',f'>={th}')
        z['coverage_pct']=round(100*len(g)/len(df),2) if len(df) else 0.0
        rows.append(z)
    mom=df.momentum_12w_pct
    for lo,hi,name in [(-np.inf,20,'<20'),(20,30,'20-30'),(30,40,'30-40'),(40,60,'40-60'),(60,np.inf,'>=60')]:
        m=mom.notna()&(mom>=lo)&(mom<hi)
        rows.append(summarize_bucket(df[m],'momentum_12w_pct_v2',name))
    return pd.DataFrame(rows)


def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger)
    if df.empty: raise RuntimeError('no PIT-enriched trades')
    df['tech_growth_score_pit_v2']=df.apply(score_row,axis=1)
    real=df[~df.endpoint_mark.astype(bool)].copy(); buckets=bucketize(real)
    coverage=100*real.consensus_upside_pit_pct.notna().mean() if len(real) else 0.0
    errors=[]
    if coverage!=0.0: errors.append('unexpected historical consensus values')
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    if not bool(df.feature_timestamp_le_signal.all()): errors.append('feature timestamp after signal')
    nonempty=buckets[(buckets.dimension=='tech_growth_score_v2')&(buckets.trades>0)]
    max_share=float(nonempty.trades.max()/real.shape[0]) if len(nonempty) and len(real) else 1.0
    if max_share>0.85: errors.append(f'V2 score still saturated max_bucket_share={max_share:.3f}')
    payload={
      'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V2',
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),'realised_trades':int(len(real)),
      'consensus_upside_pit_coverage_pct':round(float(coverage),3),'max_score_bucket_share_pct':round(100*max_share,2),
      'score_policy':'FIXED_WIDER_PIECEWISE_BANDS_NO_OUTCOME_FITTING',
      'score_components':{'momentum_12w':25,'momentum_26w':20,'acceleration_4w':15,'breakout_prior_52w':15,'distance_sma20':15,'distance_sma50':10},
      'lookahead_controls':{'completed_week_features_only':True,'prior_52w_uses_shift_1':True,'feature_timestamp_le_signal':True,'current_consensus_backfill_forbidden':True,'locked_trade_ledger_reused':True,'outcome_fitting':False},
      'bucket_results':buckets.to_dict('records'),'validation_errors':errors,
      'limitations':['NO_VALIDATED_HISTORICAL_BOURSORAMA_CONSENSUS','TECH_SCORE_IS_PROXY_NOT_ANALYST_TARGET','V2_BANDS_ARE_EX_ANTE_NOT_OUTCOME_OPTIMIZED','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    df.to_csv(OUT_TRADES,index=False); buckets.to_csv(OUT_BUCKETS,index=False)
    lines=['# AT Weekly Growth Potential PIT V2','',f"Status: **{payload['status']}**",f"Historical consensus coverage: **{coverage:.1f}%**",f"Largest V2 score bucket: **{payload['max_score_bucket_share_pct']}%**",'',
      'V2 is a PIT technical diagnostic proxy, not an analyst target. Bands are fixed and not fitted to trade outcomes.','',
      '| Dimension | Bucket | Trades | Win % | Mean % | RR | PF | P10 % | Max loss % | Robust sample | Coverage % |','|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|']
    for r in buckets.to_dict('records'):
      lines.append(f"| {r['dimension']} | {r['bucket']} | {r['trades']} | {r['win_rate_pct']} | {r['mean_return_pct']} | {r['reward_risk']} | {r['profit_factor']} | {r['p10_return_pct']} | {r['max_loss_pct']} | {r['robust_sample']} | {r.get('coverage_pct','')} |")
    OUT_MD.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'realised_trades':payload['realised_trades'],'consensus_coverage_pct':coverage,'max_score_bucket_share_pct':payload['max_score_bucket_share_pct'],'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__': run()
