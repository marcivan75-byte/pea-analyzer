"""Research-only V14: broaden admission then rank ex-ante quality to improve capital utilization.

V13 showed that simple threshold relaxation raises volume but degrades RR. V14 therefore
keeps the locked entry/exit ledger and 9% stop, but replaces hard post-signal thresholding
with a deterministic ex-ante ranking of a broader signal pool. No trade outcome is used.

Portfolio contract: EUR60k, EUR4k/title, max 12 live, max 30 entries/year,
0.20% cost/slippage each side, hard max drawdown 12%.
Promotion: each complete year >15% net, RR >3.3, PF >=2, max DD <=12%.
Same-bank research only; not clean OOS.
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
from .at_weekly_capacity_quality_v13 import portfolio_sim, trade_metrics, ANNUAL_CAP, MAX_LIVE_POSITIONS, FULL_NOMINAL_EUR, INITIAL_CAPITAL, MAX_DRAWDOWN_PCT, COMPLETE_YEARS
from .at_weekly_staged_entry_v11 import load_daily

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_RANKED_CAPACITY_V14.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_RANKED_CAPACITY_V14.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_RANKED_CAPACITY_V14_TRADES.csv'
OUT_EQUITY=OUTDIR/'AT_WEEKLY_RANKED_CAPACITY_V14_EQUITY.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_RANKED_CAPACITY_V14.md'

CANDIDATES=[
 {'name':'BROAD_QM_CD0_W2','pool':'ALL','cooldown_weeks':0,'weekly_cap':2,'rank':'QM'},
 {'name':'BROAD_QM_CD4_W2','pool':'ALL','cooldown_weeks':4,'weekly_cap':2,'rank':'QM'},
 {'name':'BROAD_QM_CD8_W2','pool':'ALL','cooldown_weeks':8,'weekly_cap':2,'rank':'QM'},
 {'name':'MOM40_QM_CD4_W2','pool':'MOM40','cooldown_weeks':4,'weekly_cap':2,'rank':'QM'},
 {'name':'BROAD_CONT_CD4_W2','pool':'ALL','cooldown_weeks':4,'weekly_cap':2,'rank':'CONT'},
 {'name':'BROAD_BAL_CD4_W2','pool':'ALL','cooldown_weeks':4,'weekly_cap':2,'rank':'BAL'},
 {'name':'BROAD_QM_CD4_W3','pool':'ALL','cooldown_weeks':4,'weekly_cap':3,'rank':'QM'},
]

def clip01(x,lo,hi):
    try:x=float(x)
    except Exception:return 0.0
    if not np.isfinite(x):return 0.0
    return float(np.clip((x-lo)/(hi-lo),0.0,1.0))

def rank_score(r,kind):
    q=clip01(r.get('quality_score_v8',np.nan),60,100)
    m12=clip01(r.get('momentum_12w_pct',np.nan),0,150)
    m26=clip01(r.get('momentum_26w_pct',np.nan),0,250)
    br=clip01(r.get('breakout_above_prior_52w_high_pct',np.nan),-10,75)
    d50=clip01(r.get('distance_sma50_pct',np.nan),0,150)
    rsi=clip01(r.get('rsi14',np.nan),50,95)
    if kind=='QM': return 100*(0.35*q+0.15*m12+0.20*m26+0.20*br+0.10*d50)
    if kind=='CONT': return 100*(0.15*q+0.20*m12+0.30*m26+0.20*br+0.15*d50)
    return 100*(0.35*q+0.15*m12+0.15*m26+0.15*br+0.10*d50+0.10*rsi)

def select_candidate(df,c):
    z=df.copy(); z['signal_date']=pd.to_datetime(z.signal_date)
    z['quality_score_v8']=z.apply(potential_score,axis=1)
    if c['pool']=='MOM40':
        z=z[pd.to_numeric(z.momentum_12w_pct,errors='coerce')>=40.0].copy()
    z['rank_score_v14']=z.apply(lambda r:rank_score(r,c['rank']),axis=1)
    z=z.sort_values(['signal_date','symbol','rank_score_v14'],ascending=[True,True,False]).drop_duplicates(['signal_date','symbol'])
    selected=[]; annual={}; last_sym={}
    for dt,g in z.groupby('signal_date',sort=True):
        y=int(pd.Timestamp(dt).year); used=annual.get(y,0)
        if used>=ANNUAL_CAP: continue
        cand=[]
        for _,r in g.sort_values(['rank_score_v14','quality_score_v8','momentum_26w_pct'],ascending=False).iterrows():
            prev=last_sym.get(r.symbol)
            if prev is not None and (pd.Timestamp(dt)-prev).days<7*int(c['cooldown_weeks']): continue
            cand.append(r)
        if not cand: continue
        pick=pd.DataFrame(cand).head(min(int(c['weekly_cap']),ANNUAL_CAP-used))
        if pick.empty: continue
        selected.append(pick); annual[y]=used+len(pick)
        for _,r in pick.iterrows(): last_sym[r.symbol]=pd.Timestamp(dt)
    return pd.concat(selected,ignore_index=True) if selected else z.iloc[0:0].copy()

def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    errors=[]
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    daily=load_daily(); rows=[]; portfolios={}; audits=[]; equities=[]
    for c in CANDIDATES:
        s=select_candidate(df,c); tm=trade_metrics(s); ps,eq,audit=portfolio_sim(s,daily)
        annual_counts={int(y):int(n) for y,n in s.groupby(pd.to_datetime(s.signal_date).dt.year).size().items()} if not s.empty else {}
        full=[x for x in ps.get('annual',[]) if x.get('period_type')=='FULL_YEAR']
        strict_econ=bool(len(full)==len(COMPLETE_YEARS) and all(x['net_return_pct']>15.0 for x in full))
        rr_ok=bool(tm['rr'] is not None and tm['rr']>3.3); pf_ok=bool(tm['pf'] is not None and tm['pf']>=2.0)
        dd_ok=bool(ps.get('max_drawdown_pct') is not None and ps['max_drawdown_pct']>=-MAX_DRAWDOWN_PCT)
        cap_ok=bool(ps.get('max_live_positions_seen',99)<=MAX_LIVE_POSITIONS and all(v<=ANNUAL_CAP for v in annual_counts.values()))
        row={'candidate':c['name'],**tm,'annual_entry_counts':annual_counts,
             'complete_year_mean_net_pct':ps.get('complete_year_mean_net_pct'),'complete_year_min_net_pct':ps.get('complete_year_min_net_pct'),
             'max_drawdown_pct':ps.get('max_drawdown_pct'),'max_live_positions_seen':ps.get('max_live_positions_seen'),
             'cumulative_net_return_pct':ps.get('cumulative_net_return_pct'),'strict_each_complete_year_gt15':strict_econ,
             'rr_gt_3_3':rr_ok,'pf_ge_2':pf_ok,'drawdown_le_12':dd_ok,'promotion_candidate':bool(strict_econ and rr_ok and pf_ok and dd_ok and cap_ok)}
        rows.append(row); portfolios[c['name']]=ps
        if not audit.empty: audit=audit.copy(); audit['candidate']=c['name']; audits.append(audit)
        if not eq.empty: eq=eq.copy(); eq['candidate']=c['name']; equities.append(eq)
    for r in rows:
        if (r.get('max_live_positions_seen') or 0)>MAX_LIVE_POSITIONS: errors.append('max live positions exceeded')
        if any(v>ANNUAL_CAP for v in r['annual_entry_counts'].values()): errors.append('annual cap exceeded')
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_RANKED_CAPACITY_V14',
             'generated_at_utc':datetime.now(timezone.utc).isoformat(),
             'objective':'BROADEN_SIGNAL_POOL_AND_RANK_EX_ANTE_WITHOUT_RELAXING_RISK_GATES',
             'portfolio_contract':{'initial_capital_eur':INITIAL_CAPITAL,'full_nominal_per_title_eur':FULL_NOMINAL_EUR,'max_live_positions':MAX_LIVE_POSITIONS,'annual_entry_cap':ANNUAL_CAP,'max_drawdown_pct':MAX_DRAWDOWN_PCT},
             'promotion_gates':{'each_complete_year_net_gt_pct':15.0,'rr_gt':3.3,'pf_ge':2.0,'max_drawdown_abs_le_pct':12.0},
             'candidates':CANDIDATES,'results':rows,'portfolio_results':portfolios,
             'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,'future_signals_not_used_for_current_ranking':True,'outcome_not_used_in_candidate_score':True,'fixed_rank_formula_not_fitted_to_trade_outcomes':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True,'staged_entry_rejected_not_used':True,'v11_theoretical_rr_rejected_not_used':True},
             'validation_errors':sorted(set(errors)),
             'limitations':['SAME_BANK_RESEARCH_NOT_CLEAN_OOS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FUNDAMENTAL_PIT_YET','2026_IS_YTD','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    pd.DataFrame(rows).to_csv(OUT_CSV,index=False)
    (pd.concat(audits,ignore_index=True) if audits else pd.DataFrame()).to_csv(OUT_TRADES,index=False)
    (pd.concat(equities,ignore_index=True) if equities else pd.DataFrame()).to_csv(OUT_EQUITY,index=False)
    lines=['# AT Weekly V14 — Ranked capacity under PEA60k','',f"Status: **{payload['status']}**",'',
           'Hard promotion: each complete year >15% net, RR >3.3, PF >=2, max DD <=12%.','',
           '| Candidate | Trades | RR | PF | Mean trade % | Full-year min % | Full-year mean % | Max DD % | Max live | Promotion |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        lines.append(f"| {r['candidate']} | {r['trades']} | {r['rr']} | {r['pf']} | {r['mean_trade_return_pct']} | {r['complete_year_min_net_pct']} | {r['complete_year_mean_net_pct']} | {r['max_drawdown_pct']} | {r['max_live_positions_seen']} | {r['promotion_candidate']} |")
    lines += ['','2026 is YTD. Rank formulas are fixed ex-ante technical composites; no outcome-derived threshold is used.']
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'results':rows,'validation_errors':payload['validation_errors']},indent=2,ensure_ascii=False))
    return payload

if __name__=='__main__': run()
