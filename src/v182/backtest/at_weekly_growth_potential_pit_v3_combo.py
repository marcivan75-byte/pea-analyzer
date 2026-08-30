"""Research-only PIT growth V3: combination robustness study.

V3 does not alter locked entry or exit logic. It reuses the locked realised trade ledger
and PIT features, then evaluates a small, pre-declared family of candidate pre-entry
gates combining V2 score and 12-week momentum. Because the hypotheses were motivated
by V2 results on the same historical bank, this is a robustness study, not clean OOS.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_growth_potential_pit_v1 import enrich_trades, summarize_bucket
from .at_weekly_growth_potential_pit_v2 import score_row
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V3_COMBO.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V3_COMBO.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V3_COMBO.md'

CANDIDATES={
 'BASELINE': lambda d: pd.Series(True,index=d.index),
 'MOM12_GE40': lambda d: d.momentum_12w_pct>=40,
 'SCORE_GE60': lambda d: d.tech_growth_score_pit_v2>=60,
 'SCORE_GE60_MOM12_GE40': lambda d: (d.tech_growth_score_pit_v2>=60)&(d.momentum_12w_pct>=40),
 'SCORE_GE70_MOM12_GE40': lambda d: (d.tech_growth_score_pit_v2>=70)&(d.momentum_12w_pct>=40),
 'SCORE_GE60_MOM12_GE60': lambda d: (d.tech_growth_score_pit_v2>=60)&(d.momentum_12w_pct>=60),
}


def metrics(df,label,segment):
    z=summarize_bucket(df,label,segment)
    z['candidate']=label; z['segment']=segment
    return z


def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger)
    if df.empty: raise RuntimeError('no PIT-enriched trades')
    df=df[~df.endpoint_mark.astype(bool)].copy()
    df['tech_growth_score_pit_v2']=df.apply(score_row,axis=1)
    df['exit_date']=pd.to_datetime(df.exit_date)
    # Fixed descriptive temporal segmentation. Not labelled OOS because V2 hypotheses
    # already saw the full bank.
    split=pd.Timestamp('2025-07-01')
    segments={
      'ALL': pd.Series(True,index=df.index),
      'EARLY_THROUGH_2025H1': df.exit_date<split,
      'LATE_FROM_2025H2': df.exit_date>=split,
    }
    rows=[]
    for name,fn in CANDIDATES.items():
        gate=fn(df).fillna(False)
        for seg,sm in segments.items():
            g=df[gate & sm]
            r=metrics(g,name,seg)
            r['coverage_pct']=round(100*len(g)/max(1,int(sm.sum())),2)
            rows.append(r)
        for q,qm in df.groupby(df.exit_date.dt.to_period('Q').astype(str)).groups.items():
            idx=pd.Index(qm); g=df.loc[idx][gate.loc[idx]]
            r=metrics(g,name,q); r['coverage_pct']=round(100*len(g)/max(1,len(idx)),2); rows.append(r)
    res=pd.DataFrame(rows)
    base=res[res.candidate=='BASELINE'].set_index('segment')
    summary=[]
    for name in CANDIDATES:
        if name=='BASELINE': continue
        x=res[res.candidate==name].set_index('segment')
        allr=x.loc['ALL']; early=x.loc['EARLY_THROUGH_2025H1']; late=x.loc['LATE_FROM_2025H2']
        b_all=base.loc['ALL']; b_early=base.loc['EARLY_THROUGH_2025H1']; b_late=base.loc['LATE_FROM_2025H2']
        def imp(a,b,k):
            av=a[k]; bv=b[k]
            return None if pd.isna(av) or pd.isna(bv) else round(float(av-bv),3)
        quarters=x[~x.index.isin(['ALL','EARLY_THROUGH_2025H1','LATE_FROM_2025H2'])]
        robust_q=quarters[quarters.trades>=20]
        positive_q=int((robust_q.mean_return_pct>0).sum()) if len(robust_q) else 0
        pf_gt1_q=int((robust_q.profit_factor>1).sum()) if len(robust_q) else 0
        summary.append({
          'candidate':name,'trades_all':int(allr.trades),'coverage_all_pct':float(allr.coverage_pct),
          'mean_all_pct':allr.mean_return_pct,'pf_all':allr.profit_factor,'rr_all':allr.reward_risk,
          'delta_mean_all_vs_base':imp(allr,b_all,'mean_return_pct'),'delta_pf_all_vs_base':imp(allr,b_all,'profit_factor'),'delta_rr_all_vs_base':imp(allr,b_all,'reward_risk'),
          'early_trades':int(early.trades),'early_mean':early.mean_return_pct,'early_pf':early.profit_factor,'early_rr':early.reward_risk,
          'late_trades':int(late.trades),'late_mean':late.mean_return_pct,'late_pf':late.profit_factor,'late_rr':late.reward_risk,
          'delta_early_mean_vs_base':imp(early,b_early,'mean_return_pct'),'delta_late_mean_vs_base':imp(late,b_late,'mean_return_pct'),
          'robust_quarters_n':int(len(robust_q)),'positive_robust_quarters_n':positive_q,'pf_gt1_robust_quarters_n':pf_gt1_q,
          'sample_guard':bool(allr.trades>=40 and early.trades>=20 and late.trades>=20),
          'direction_guard':bool((not pd.isna(early.mean_return_pct)) and (not pd.isna(late.mean_return_pct)) and early.mean_return_pct>0 and late.mean_return_pct>0),
        })
    s=pd.DataFrame(summary)
    errors=[]
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    if not bool(df.feature_timestamp_le_signal.all()): errors.append('feature timestamp after signal')
    payload={
      'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_GROWTH_POTENTIAL_PIT_V3_COMBO',
      'generated_at_utc':datetime.now(timezone.utc).isoformat(),'realised_trades':int(len(df)),
      'hypothesis_status':'DATA_INFORMED_FROM_V2_SAME_BANK_NOT_CLEAN_OOS',
      'candidate_family':list(CANDIDATES.keys()),'temporal_split':'2025-07-01',
      'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True},
      'summary':s.to_dict('records'),'validation_errors':errors,
      'limitations':['V2_HYPOTHESES_SAW_FULL_HISTORY','NOT_CLEAN_OOS','NO_HISTORICAL_ANALYST_CONSENSUS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    res.to_csv(OUT_CSV,index=False)
    lines=['# AT Weekly Growth Potential PIT V3 Combo','',f"Status: **{payload['status']}**",'',
      'V3 is a robustness study, not clean OOS: candidate thresholds were motivated by V2 results on the same historical bank.','',
      '| Candidate | Trades | Coverage % | Mean % | PF | RR | Early mean | Late mean | Sample guard | Direction guard |',
      '|---|---:|---:|---:|---:|---:|---:|---:|---|---|']
    for r in s.to_dict('records'):
      lines.append(f"| {r['candidate']} | {r['trades_all']} | {r['coverage_all_pct']} | {r['mean_all_pct']} | {r['pf_all']} | {r['rr_all']} | {r['early_mean']} | {r['late_mean']} | {r['sample_guard']} | {r['direction_guard']} |")
    OUT_MD.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'status':payload['status'],'realised_trades':payload['realised_trades'],'candidates':len(CANDIDATES)-1,'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__': run()
