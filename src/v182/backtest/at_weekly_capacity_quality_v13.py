"""Research-only V13: increase high-quality opportunity capacity under the locked PEA60k contract.

V12 showed good RR/PF and low drawdown for FULL_J0, but too few trades and too much idle cash.
V13 therefore changes only the post-signal admission layer. Locked raw entries, 9% protective
stop and D exit architecture are unchanged. No staged entry and no V11 theoretical-RR filter are used.

Hard portfolio contract:
- EUR60,000 initial capital
- EUR4,000 per accepted title
- max 12 live positions
- max 30 entries per calendar year
- research cost/slippage 0.20% each side
- max drawdown <=12%

Promotion requires, on complete years 2024-2025:
- every complete year net return >15%
- ex-post RR >3.3
- PF >=2.0
- max drawdown <=12%

Candidate families are coarse, predeclared and use only completed-week PIT technical fields.
Same-bank results remain exploratory, not clean OOS.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_selection_quality_v8_top30 import POLICIES, potential_score, passes
from .at_weekly_growth_potential_pit_v1 import enrich_trades, summarize_bucket
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger
from .at_weekly_staged_entry_v11 import load_daily, build_events

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_CAPACITY_QUALITY_V13.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_CAPACITY_QUALITY_V13.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_CAPACITY_QUALITY_V13_TRADES.csv'
OUT_EQUITY=OUTDIR/'AT_WEEKLY_CAPACITY_QUALITY_V13_EQUITY.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_CAPACITY_QUALITY_V13.md'

INITIAL_CAPITAL=60000.0
FULL_NOMINAL_EUR=4000.0
MAX_LIVE_POSITIONS=12
ANNUAL_CAP=30
COST_SIDE_PCT=0.20
MAX_DRAWDOWN_PCT=12.0
COMPLETE_YEARS=(2024,2025)

P1=next(p for p in POLICIES if p['name']=='P1_SCORE85_CD8')
P2=next(p for p in POLICIES if p['name']=='P2_SCORE88_CD12')

# Coarse predeclared families. Thresholds deliberately avoid V9 exact stop/winner medians.
CANDIDATES=[
 {'name':'P1_BASE_W1','policy':'P1','weekly_cap':1},
 {'name':'P1_BASE_W2','policy':'P1','weekly_cap':2},
 {'name':'P2_BASE_W1','policy':'P2','weekly_cap':1},
 {'name':'P2_BASE_W2','policy':'P2','weekly_cap':2},
 {'name':'P1_BREAKOUT10_W2','policy':'P1','weekly_cap':2,'breakout_min':10.0},
 {'name':'P1_BREAKOUT20_W2','policy':'P1','weekly_cap':2,'breakout_min':20.0},
 {'name':'P1_MOM26_100_W2','policy':'P1','weekly_cap':2,'m26_min':100.0},
 {'name':'P1_MOM26_125_W2','policy':'P1','weekly_cap':2,'m26_min':125.0},
 {'name':'P1_DUAL10_100_W2','policy':'P1','weekly_cap':2,'breakout_min':10.0,'m26_min':100.0},
 {'name':'P1_OR20_125_W2','policy':'P1','weekly_cap':2,'or_strength':True},
]

def ge(r,k,v):
    x=r.get(k,np.nan)
    return bool(np.isfinite(x) and float(x)>=float(v))

def extra_pass(r,c):
    if c.get('or_strength'):
        return ge(r,'breakout_above_prior_52w_high_pct',20.0) or ge(r,'momentum_26w_pct',125.0)
    if 'breakout_min' in c and not ge(r,'breakout_above_prior_52w_high_pct',c['breakout_min']): return False
    if 'm26_min' in c and not ge(r,'momentum_26w_pct',c['m26_min']): return False
    return True

def select_candidate(df,c):
    p=P1 if c['policy']=='P1' else P2
    z=df.copy(); z['signal_date']=pd.to_datetime(z.signal_date)
    z['quality_score_v8']=z.apply(potential_score,axis=1); z['potential_score_v7']=z['quality_score_v8']
    z=z[z.apply(lambda r: passes(r,p) and extra_pass(r,c),axis=1)].copy()
    z=z.sort_values(['signal_date','symbol','quality_score_v8'],ascending=[True,True,False]).drop_duplicates(['signal_date','symbol'])
    selected=[]; annual={}; last_sym={}
    for dt,g in z.groupby('signal_date',sort=True):
        y=int(pd.Timestamp(dt).year); used=annual.get(y,0)
        if used>=ANNUAL_CAP: continue
        cand=[]
        for _,r in g.sort_values(['quality_score_v8','momentum_12w_pct','momentum_26w_pct'],ascending=False).iterrows():
            prev=last_sym.get(r.symbol)
            if prev is not None and (pd.Timestamp(dt)-prev).days < 7*int(p['cooldown_weeks']): continue
            cand.append(r)
        if not cand: continue
        pick=pd.DataFrame(cand).head(min(int(c['weekly_cap']),ANNUAL_CAP-used))
        if pick.empty: continue
        selected.append(pick); annual[y]=used+len(pick)
        for _,r in pick.iterrows(): last_sym[r.symbol]=pd.Timestamp(dt)
    return pd.concat(selected,ignore_index=True) if selected else z.iloc[0:0].copy()

def trade_metrics(s):
    if s.empty:
        return {'trades':0,'rr':None,'pf':None,'mean_trade_return_pct':None,'stop_count':0,'stop_rate_pct':None,'d_count':0,'d_rate_pct':None}
    m=summarize_bucket(s,'v13','all'); n=len(s); cat=s.exit_category.astype(str)
    st=int((cat=='PROTECTIVE_STOP').sum()); d=int((cat=='D_REVERSAL').sum())
    return {'trades':int(n),'rr':m['reward_risk'],'pf':m['profit_factor'],'mean_trade_return_pct':m['mean_return_pct'],
            'stop_count':st,'stop_rate_pct':round(100*st/n,2),'d_count':d,'d_rate_pct':round(100*d/n,2)}

def portfolio_sim(rows,daily):
    ev,audit=build_events(rows,daily,0)
    if ev.empty: return {},pd.DataFrame(),audit
    dates=sorted(set(pd.Timestamp(x) for h in daily.values() for x in h.index
                     if pd.Timestamp('2024-01-01')<=pd.Timestamp(x)<=pd.Timestamp('2026-12-31')))
    bydate={d:g.sort_values('order') for d,g in ev.groupby('date')}
    cash=INITIAL_CAPITAL; pos={}; eq=[]; costs=0.0; cap_rej=0; cash_rej=0; max_live=0
    for dt in dates:
        dayev=bydate.get(dt)
        if dayev is not None:
            for _,e in dayev.iterrows():
                typ=e['type']; tid=e['tid']; px=float(e['price'])
                if typ=='EXIT':
                    if tid not in pos: continue
                    gross=pos[tid]['shares']*px; fee=gross*COST_SIDE_PCT/100.0
                    costs+=fee; cash+=gross-fee; pos.pop(tid,None)
                elif typ=='ENTRY':
                    if len(pos)>=MAX_LIVE_POSITIONS: cap_rej+=1; continue
                    need=FULL_NOMINAL_EUR*(1+COST_SIDE_PCT/100.0)
                    if cash+1e-9<need: cash_rej+=1; continue
                    fee=FULL_NOMINAL_EUR*COST_SIDE_PCT/100.0; costs+=fee; cash-=FULL_NOMINAL_EUR+fee
                    pos[tid]={'symbol':e['symbol'],'shares':FULL_NOMINAL_EUR/px}
        max_live=max(max_live,len(pos))
        mtm=0.0
        for v in pos.values():
            h=daily.get(v['symbol']); q=h[h.index<=dt] if h is not None else pd.DataFrame()
            if not q.empty: mtm+=v['shares']*float(q.close.iloc[-1])
        eq.append({'date':dt,'nav_eur':cash+mtm,'cash_eur':cash,'open_positions':len(pos),'gross_mtm_eur':mtm})
    e=pd.DataFrame(eq); dd=(e.nav_eur/e.nav_eur.cummax()-1)*100.0
    annual=[]
    for y in [2024,2025,2026]:
        q=e[e.date.dt.year==y]
        if q.empty: continue
        prior=e[e.date<q.date.min()]
        s=INITIAL_CAPITAL if prior.empty else float(prior.nav_eur.iloc[-1]); z=float(q.nav_eur.iloc[-1])
        annual.append({'year':y,'period_type':'FULL_YEAR' if y in COMPLETE_YEARS else 'YTD',
                       'net_return_pct':round(100*(z/s-1),3),'start_nav_eur':round(s,2),'end_nav_eur':round(z,2)})
    full=[x for x in annual if x['year'] in COMPLETE_YEARS]
    return {'annual':annual,
            'complete_year_mean_net_pct':round(float(np.mean([x['net_return_pct'] for x in full])),3) if full else None,
            'complete_year_min_net_pct':round(float(np.min([x['net_return_pct'] for x in full])),3) if full else None,
            'ending_nav_eur':round(float(e.nav_eur.iloc[-1]),2),
            'cumulative_net_return_pct':round(100*(float(e.nav_eur.iloc[-1])/INITIAL_CAPITAL-1),3),
            'max_drawdown_pct':round(float(dd.min()),3),'max_live_positions_seen':int(max_live),
            'capacity_rejections':int(cap_rej),'cash_rejections':int(cash_rej),'transaction_cost_eur':round(costs,2)},e,audit

def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    errors=[]
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    daily=load_daily(); rows=[]; portfolios={}; audits=[]; equities=[]
    for c in CANDIDATES:
        s=select_candidate(df,c); tm=trade_metrics(s); ps,eq,audit=portfolio_sim(s,daily)
        full=ps.get('annual',[]); full=[x for x in full if x.get('period_type')=='FULL_YEAR']
        annual_counts={int(y):int(n) for y,n in s.groupby(pd.to_datetime(s.signal_date).dt.year).size().items()} if not s.empty else {}
        strict_econ=bool(len(full)==len(COMPLETE_YEARS) and all(x['net_return_pct']>15.0 for x in full))
        rr_ok=bool(tm['rr'] is not None and tm['rr']>3.3); pf_ok=bool(tm['pf'] is not None and tm['pf']>=2.0)
        dd_ok=bool(ps.get('max_drawdown_pct') is not None and ps['max_drawdown_pct']>=-MAX_DRAWDOWN_PCT)
        cap_ok=bool(ps.get('max_live_positions_seen',99)<=MAX_LIVE_POSITIONS and all(v<=ANNUAL_CAP for v in annual_counts.values()))
        promotion=bool(strict_econ and rr_ok and pf_ok and dd_ok and cap_ok)
        row={'candidate':c['name'],**tm,'annual_entry_counts':annual_counts,
             'complete_year_mean_net_pct':ps.get('complete_year_mean_net_pct'),'complete_year_min_net_pct':ps.get('complete_year_min_net_pct'),
             'max_drawdown_pct':ps.get('max_drawdown_pct'),'max_live_positions_seen':ps.get('max_live_positions_seen'),
             'cumulative_net_return_pct':ps.get('cumulative_net_return_pct'),'strict_each_complete_year_gt15':strict_econ,
             'rr_gt_3_3':rr_ok,'pf_ge_2':pf_ok,'drawdown_le_12':dd_ok,'promotion_candidate':promotion}
        rows.append(row); portfolios[c['name']]=ps
        if not audit.empty: audit=audit.copy(); audit['candidate']=c['name']; audits.append(audit)
        if not eq.empty: eq=eq.copy(); eq['candidate']=c['name']; equities.append(eq)
    for r in rows:
        if (r.get('max_live_positions_seen') or 0)>MAX_LIVE_POSITIONS: errors.append('max live positions exceeded')
        if any(v>ANNUAL_CAP for v in r['annual_entry_counts'].values()): errors.append('annual cap exceeded')
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_CAPACITY_QUALITY_V13',
             'generated_at_utc':datetime.now(timezone.utc).isoformat(),
             'objective':'INCREASE_HIGH_QUALITY_CAPITAL_UTILIZATION_WITHOUT_RELAXING_HARD_RISK_GATES',
             'portfolio_contract':{'initial_capital_eur':INITIAL_CAPITAL,'full_nominal_per_title_eur':FULL_NOMINAL_EUR,
                                   'max_live_positions':MAX_LIVE_POSITIONS,'annual_entry_cap':ANNUAL_CAP,
                                   'cost_and_slippage_each_side_pct':COST_SIDE_PCT,'max_drawdown_pct':MAX_DRAWDOWN_PCT},
             'promotion_gates':{'each_complete_year_net_gt_pct':15.0,'rr_gt':3.3,'pf_ge':2.0,'max_drawdown_abs_le_pct':12.0},
             'candidates':CANDIDATES,'results':rows,'portfolio_results':portfolios,
             'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,
                                   'future_signals_not_used_for_current_ranking':True,'outcome_not_used_in_candidate_score':True,
                                   'v9_exact_medians_not_used_as_thresholds':True,'locked_trade_ledger_reused':True,
                                   'locked_entry_exit_unchanged':True,'staged_entry_rejected_not_used':True,
                                   'v11_theoretical_rr_rejected_not_used':True},
             'validation_errors':sorted(set(errors)),
             'limitations':['SAME_BANK_RESEARCH_NOT_CLEAN_OOS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE',
                            'NO_FUNDAMENTAL_PIT_YET','COST_ASSUMPTION_NOT_BROKER_SPECIFIC','2026_IS_YTD','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    pd.DataFrame(rows).to_csv(OUT_CSV,index=False)
    (pd.concat(audits,ignore_index=True) if audits else pd.DataFrame()).to_csv(OUT_TRADES,index=False)
    (pd.concat(equities,ignore_index=True) if equities else pd.DataFrame()).to_csv(OUT_EQUITY,index=False)
    lines=['# AT Weekly V13 — Quality capacity under PEA60k','',f"Status: **{payload['status']}**",'',
           'Hard promotion: each complete year >15% net, RR >3.3, PF >=2, max DD <=12%.','',
           '| Candidate | Trades | RR | PF | Mean trade % | Full-year min % | Full-year mean % | Max DD % | Max live | Promotion |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        lines.append(f"| {r['candidate']} | {r['trades']} | {r['rr']} | {r['pf']} | {r['mean_trade_return_pct']} | {r['complete_year_min_net_pct']} | {r['complete_year_mean_net_pct']} | {r['max_drawdown_pct']} | {r['max_live_positions_seen']} | {r['promotion_candidate']} |")
    lines += ['','2026 is YTD only. Candidate thresholds are predeclared coarse technical rules; this is not clean OOS.']
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'results':rows,'validation_errors':payload['validation_errors']},indent=2,ensure_ascii=False))
    return payload

if __name__=='__main__': run()
