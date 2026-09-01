from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
import v23_cross_sectional_momentum_baseline_v4 as core

SMA_DAYS=200


def build_signals_trend(z):
    parts=[]
    for isin,g in z.groupby('isin',sort=False):
        g=g.sort_values('date').copy()
        g['mom']=g.close.shift(core.SKIP)/g.close.shift(core.LOOKBACK)-1.0
        g['sma200']=g.close.rolling(SMA_DAYS,min_periods=SMA_DAYS).mean().shift(1)
        g['obs']=np.arange(len(g))+1
        g['month']=g.date.dt.to_period('M')
        parts.append(g[['isin','date','close','mom','sma200','obs','month']])
    x=pd.concat(parts,ignore_index=True)
    anchors=x.groupby('month',as_index=False).date.max().rename(columns={'date':'signal_date'})
    last=x.sort_values(['isin','month','date']).groupby(['isin','month'],as_index=False).tail(1)
    s=last.merge(anchors,on='month',how='left')
    s=s[(s.obs>=core.MIN_OBS)&s.mom.notna()&s.sma200.notna()&(s.date<=s.signal_date)&(s.close>s.sma200)].copy()
    s['rank']=s.groupby('signal_date').mom.rank(method='first',ascending=False)
    s=s[s['rank']<=core.TOP_N].sort_values(['signal_date','rank','isin'])
    counts=s.groupby('signal_date').size()
    bad=counts[counts!=core.TOP_N]
    if len(bad):
        raise SystemExit(f'BLOCK_TARGET_COUNT_TREND {bad.head(10).to_dict()}')
    return s[['signal_date','date','isin','close','mom','sma200','rank']].rename(columns={'date':'feature_date'})


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--history',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    z=core.norm(pd.read_parquet(a.history))
    baseline=core.build_signals(z)
    trend=build_signals_trend(z)
    be,bt,bm=core.simulate(z,baseline,False)
    te,tt,tm=core.simulate(z,trend,False)
    tse,tst,tsm=core.simulate(z,trend,True)
    a.out_dir.mkdir(parents=True,exist_ok=True)
    trend.to_csv(a.out_dir/'TREND_INCREMENTAL_SIGNALS_PRE2023.csv',index=False)
    te.to_csv(a.out_dir/'TREND_INCREMENTAL_EQUITY_BASE_PRE2023.csv',index=False)
    tt.to_csv(a.out_dir/'TREND_INCREMENTAL_TRADES_BASE_PRE2023.csv',index=False)
    report={
      'version':'TABPORT_V23_TREND_INCREMENTAL_1_FROZEN',
      'hypothesis':'Add one simple absolute-trend eligibility rule to the frozen 12-1 momentum baseline: latest PIT close must be above prior 200-session SMA; rank eligible names by unchanged 12-1 momentum; top 10 monthly.',
      'governance':{'holdout_2023_2026_accessed':False,'variant_count':1,'tuning':False,'sma_days':SMA_DAYS,'survivorship_bias':True},
      'baseline_momentum_base':bm,
      'trend_plus_momentum_base':tm,
      'trend_plus_momentum_stress':tsm,
      'trend_plus_momentum_base_subperiods':core.sub(te),
      'trend_plus_momentum_stress_subperiods':core.sub(tse),
      'decision_deltas':{
        'cagr_pp':100*(tm['cagr']-bm['cagr']),
        'max_drawdown_pp':100*(tm['max_drawdown']-bm['max_drawdown']),
        'volatility_pp':100*(tm['annualized_volatility']-bm['annualized_volatility'])
      },
      'decision_rule':'Retain trend block only if it improves robustness/risk materially without destroying return; no threshold/SMA optimization after observing this test.',
      'warnings':['Historical universe has survivorship bias','Price-only reconstruction','This is one frozen incremental hypothesis, not a parameter search']
    }
    (a.out_dir/'TREND_INCREMENTAL_REPORT_PRE2023.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print(json.dumps(report,indent=2,default=str))

if __name__=='__main__': main()
