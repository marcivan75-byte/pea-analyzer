"""Research-only V12: user portfolio contract = EUR60k, max 12 live positions.

This stage corrects V11 portfolio sizing without changing the locked trading exits.
Each accepted title plans EUR4,000 maximum exposure: EUR1,000 probe at J, then EUR3,000
only after the predeclared J+1/J+2/J+3 confirmation. The full-entry control invests
EUR4,000 at J. Cash is never borrowed and no more than 12 positions may be alive.

Promotion gates are intentionally hard:
- ex-post trade RR > 3.3
- PF >= 2.0
- mean COMPLETE-calendar-year net portfolio return > 15%
- positive return in each evaluated complete year
- maximum drawdown <= 12%
2026 is YTD and is reported but is not treated as a complete calendar year.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from . import at_weekly_staged_entry_v11 as v11
from .at_weekly_selection_portfolio_v10 import CANDIDATES as V10_CANDIDATES, select_candidate
from .at_weekly_growth_potential_pit_v1 import enrich_trades
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_STAGED_ENTRY_V12_PEA60K.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_STAGED_ENTRY_V12_PEA60K.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_STAGED_ENTRY_V12_PEA60K_TRADES.csv'
OUT_EQUITY=OUTDIR/'AT_WEEKLY_STAGED_ENTRY_V12_PEA60K_EQUITY.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_STAGED_ENTRY_V12_PEA60K.md'

INITIAL_CAPITAL=60000.0
MAX_LIVE_POSITIONS=12
FULL_NOMINAL_EUR=4000.0
PROBE_EUR=1000.0
ADD_EUR=3000.0
COST_SIDE_PCT=0.20
MAX_DRAWDOWN_PCT=12.0
COMPLETE_YEARS=(2024,2025)
BASES=['BREAKOUT30','STRENGTH_DUAL']
RR_FLOORS=[None,3.3]
STAGES=v11.STAGES


def portfolio_sim(rows,daily,lag):
    ev,audit=v11.build_events(rows,daily,lag)
    if ev.empty:
        return {},pd.DataFrame(),audit
    dates=sorted(set(pd.Timestamp(x) for h in daily.values() for x in h.index
                     if pd.Timestamp('2024-01-01')<=pd.Timestamp(x)<=pd.Timestamp('2026-12-31')))
    bydate={d:g.sort_values('order') for d,g in ev.groupby('date')}
    cash=INITIAL_CAPITAL; pos={}; eq=[]; costs=0.0; rejected_capacity=0; rejected_cash=0
    max_live_seen=0
    for dt in dates:
        dayev=bydate.get(dt)
        if dayev is not None:
            for _,e in dayev.iterrows():
                typ=e['type']; tid=e['tid']; px=float(e['price'])
                if typ=='EXIT':
                    if tid not in pos: continue
                    gross=pos[tid]['shares']*px
                    fee=gross*COST_SIDE_PCT/100.0; costs+=fee; cash+=gross-fee
                    pos.pop(tid,None)
                elif typ=='ENTRY':
                    if len(pos)>=MAX_LIVE_POSITIONS:
                        rejected_capacity+=1; continue
                    amount=FULL_NOMINAL_EUR if lag==0 else PROBE_EUR
                    need=amount*(1+COST_SIDE_PCT/100.0)
                    if cash+1e-9<need:
                        rejected_cash+=1; continue
                    fee=amount*COST_SIDE_PCT/100.0; costs+=fee; cash-=amount+fee
                    pos[tid]={'symbol':e['symbol'],'shares':amount/px,'planned':FULL_NOMINAL_EUR,'added':lag==0}
                elif typ=='ADD':
                    if tid not in pos or pos[tid]['added']: continue
                    amount=ADD_EUR
                    need=amount*(1+COST_SIDE_PCT/100.0)
                    if cash+1e-9<need:
                        rejected_cash+=1; continue
                    fee=amount*COST_SIDE_PCT/100.0; costs+=fee; cash-=amount+fee
                    pos[tid]['shares']+=amount/px; pos[tid]['added']=True
        max_live_seen=max(max_live_seen,len(pos))
        mtm=0.0
        for v in pos.values():
            h=daily.get(v['symbol']); q=h[h.index<=dt] if h is not None else pd.DataFrame()
            if not q.empty: mtm+=v['shares']*float(q.close.iloc[-1])
        eq.append({'date':dt,'nav_eur':cash+mtm,'cash_eur':cash,'open_positions':len(pos),'gross_mtm_eur':mtm})
    e=pd.DataFrame(eq)
    peak=e.nav_eur.cummax(); dd=(e.nav_eur/peak-1)*100.0
    annual=[]
    for y in [2024,2025,2026]:
        q=e[e.date.dt.year==y]
        if q.empty: continue
        prior=e[e.date<q.date.min()]
        start_nav=INITIAL_CAPITAL if prior.empty else float(prior.nav_eur.iloc[-1])
        end_nav=float(q.nav_eur.iloc[-1])
        annual.append({'year':y,'period_type':'FULL_YEAR' if y in COMPLETE_YEARS else 'YTD',
                       'net_return_pct':round(100*(end_nav/start_nav-1),3),
                       'start_nav_eur':round(start_nav,2),'end_nav_eur':round(end_nav,2)})
    full=[x for x in annual if x['year'] in COMPLETE_YEARS]
    stats={'annual':annual,
           'complete_year_net_mean_pct':round(float(np.mean([x['net_return_pct'] for x in full])),3) if full else None,
           'complete_year_net_min_pct':round(float(np.min([x['net_return_pct'] for x in full])),3) if full else None,
           'positive_complete_years':sum(x['net_return_pct']>0 for x in full),
           'ending_nav_eur':round(float(e.nav_eur.iloc[-1]),2),
           'cumulative_net_return_pct':round(100*(float(e.nav_eur.iloc[-1])/INITIAL_CAPITAL-1),3),
           'max_drawdown_pct':round(float(dd.min()),3),'max_live_positions_seen':int(max_live_seen),
           'capacity_rejections':int(rejected_capacity),'cash_rejections':int(rejected_cash),
           'total_transaction_cost_eur':round(costs,2)}
    return stats,e,audit


def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    for c in ['theoretical_target_pct_v11','theoretical_rr_v11','atr20_pct_v11','prior52_range_pct_v11']:
        df[c]=np.nan
    for i,r in df.iterrows():
        for k,val in v11.exante_features(bars,r).items(): df.at[i,k]=val
    daily=v11.load_daily(); v10map={c['name']:c for c in V10_CANDIDATES}
    results=[]; portfolios={}; all_audit=[]; all_eq=[]; errors=[]
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): errors.append('lookahead feature timestamp')
    for base in BASES:
        selected=select_candidate(df,v10map[base]).copy()
        for floor in RR_FLOORS:
            q=selected.copy(); rrlabel='NO_RR_FLOOR' if floor is None else 'RR_GE_3_3'
            if floor is not None:
                q=q[pd.to_numeric(q.theoretical_rr_v11,errors='coerce')>=floor].copy()
            for st in STAGES:
                lag=int(st['lag_sessions']); key=f"{base}|{rrlabel}|{st['name']}"
                stats,eq,audit=portfolio_sim(q,daily,lag); m=v11.metric_from_audit(audit)
                full=stats.get('annual',[]); full=[x for x in full if x.get('period_type')=='FULL_YEAR']
                economic=bool(len(full)==len(COMPLETE_YEARS) and stats.get('complete_year_net_mean_pct') is not None
                              and stats['complete_year_net_mean_pct']>15.0 and all(x['net_return_pct']>0 for x in full))
                rr_ok=bool(m.get('rr') is not None and m['rr']>3.3)
                pf_ok=bool(m.get('pf') is not None and m['pf']>=2.0)
                dd_ok=bool(stats.get('max_drawdown_pct') is not None and stats['max_drawdown_pct']>=-MAX_DRAWDOWN_PCT)
                cap_ok=bool(stats.get('max_live_positions_seen',99)<=MAX_LIVE_POSITIONS)
                promotion=bool(economic and rr_ok and pf_ok and dd_ok and cap_ok)
                results.append({'configuration':key,'base':base,'rr_floor':floor,'stage':st['name'],**m,
                                'complete_year_net_mean_pct':stats.get('complete_year_net_mean_pct'),
                                'complete_year_net_min_pct':stats.get('complete_year_net_min_pct'),
                                'max_drawdown_pct':stats.get('max_drawdown_pct'),'max_live_positions_seen':stats.get('max_live_positions_seen'),
                                'cumulative_net_return_pct':stats.get('cumulative_net_return_pct'),
                                'economic_gt15':economic,'rr_gt_3_3':rr_ok,'pf_ge_2':pf_ok,'drawdown_le_12':dd_ok,
                                'promotion_candidate':promotion})
                portfolios[key]=stats
                if not audit.empty:
                    audit=audit.copy(); audit['configuration']=key; all_audit.append(audit)
                if not eq.empty:
                    eq=eq.copy(); eq['configuration']=key; all_eq.append(eq)
    if any((r.get('max_live_positions_seen') or 0)>MAX_LIVE_POSITIONS for r in results): errors.append('max live positions exceeded')
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_STAGED_ENTRY_V12_PEA60K',
             'generated_at_utc':datetime.now(timezone.utc).isoformat(),
             'portfolio_contract':{'initial_capital_eur':INITIAL_CAPITAL,'max_live_positions':MAX_LIVE_POSITIONS,
                                   'full_nominal_per_title_eur':FULL_NOMINAL_EUR,'probe_eur':PROBE_EUR,'conditional_add_eur':ADD_EUR,
                                   'cost_and_slippage_each_side_pct':COST_SIDE_PCT,'max_drawdown_pct':MAX_DRAWDOWN_PCT},
             'promotion_gates':{'complete_year_mean_net_gt_pct':15.0,'rr_gt':3.3,'pf_ge':2.0,'max_drawdown_abs_le_pct':12.0,
                                'positive_complete_years_required':len(COMPLETE_YEARS)},
             'complete_years_evaluated':list(COMPLETE_YEARS),'results':results,'portfolio_results':portfolios,
             'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,
                                   'outcome_not_used_in_exante_rr':True,'confirmation_observed_before_add':True,
                                   'add_executes_next_session_open':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True},
             'validation_errors':errors,
             'limitations':['SAME_BANK_RESEARCH_NOT_CLEAN_OOS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE',
                            'NO_FUNDAMENTAL_PIT_YET','COST_ASSUMPTION_NOT_BROKER_SPECIFIC','2026_IS_YTD','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    pd.DataFrame(results).to_csv(OUT_CSV,index=False)
    (pd.concat(all_audit,ignore_index=True) if all_audit else pd.DataFrame()).to_csv(OUT_TRADES,index=False)
    (pd.concat(all_eq,ignore_index=True) if all_eq else pd.DataFrame()).to_csv(OUT_EQUITY,index=False)
    lines=['# AT Weekly V12 — PEA 60k / 12 positions / staged entry','',f"Status: **{payload['status']}**",'',
           'Contract: EUR60,000 capital; max 12 live positions; EUR1,000 probe + EUR3,000 conditional add; hard max drawdown 12%.','',
           '| Configuration | Trades | RR | PF | Mean trade % | Full-year mean net % | Max DD % | Max live | Promotion |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in results:
        lines.append(f"| {r['configuration']} | {r['trades']} | {r['rr']} | {r['pf']} | {r['mean_trade_return_pct']} | {r['complete_year_net_mean_pct']} | {r['max_drawdown_pct']} | {r['max_live_positions_seen']} | {r['promotion_candidate']} |")
    lines += ['','2026 is YTD only and is not counted as a complete year for the >15% annual promotion gate.','',
              'No configuration is promoted unless it simultaneously clears return, RR, PF and max-drawdown gates.']
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'results':results,'validation_errors':errors},indent=2,ensure_ascii=False))
    return payload

if __name__=='__main__': run()
