"""Research-only V10: portfolio economics + predeclared quality filters.
Primary gate: mean calendar-year net portfolio return >15%. Secondary: RR >3.3.
Locked entry/exit architecture and 9% protective stop are unchanged.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_selection_quality_v8_top30 import POLICIES, potential_score, passes, ANNUAL_CAP, WEEKLY_CAP
from .at_weekly_growth_potential_pit_v1 import enrich_trades, summarize_bucket
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_SELECTION_PORTFOLIO_V10.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_SELECTION_PORTFOLIO_V10.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_SELECTION_PORTFOLIO_V10_TRADES.csv'
OUT_EQUITY=OUTDIR/'AT_WEEKLY_SELECTION_PORTFOLIO_V10_EQUITY.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_SELECTION_PORTFOLIO_V10.md'

P2=next(p for p in POLICIES if p['name']=='P2_SCORE88_CD12')
INITIAL_CAPITAL=100000.0
MAX_POSITIONS=5
TARGET_WEIGHT=0.20
COST_SIDE_PCT=0.20
CANDIDATES=[
 {'name':'BASE_P2'},
 {'name':'BREAKOUT30','breakout_min':30.0},
 {'name':'MOM26_150','m26_min':150.0},
 {'name':'STRENGTH_DUAL','breakout_min':30.0,'m26_min':150.0},
 {'name':'RSI80','rsi_min':80.0},
]

def ge(r,k,v):
    x=r.get(k,np.nan); return bool(np.isfinite(x) and float(x)>=float(v))

def extra_pass(r,c):
    if 'breakout_min' in c and not ge(r,'breakout_above_prior_52w_high_pct',c['breakout_min']): return False
    if 'm26_min' in c and not ge(r,'momentum_26w_pct',c['m26_min']): return False
    if 'rsi_min' in c and not ge(r,'rsi14',c['rsi_min']): return False
    return True

def select_candidate(df,c):
    z=df.copy(); z['signal_date']=pd.to_datetime(z.signal_date)
    z['quality_score_v8']=z.apply(potential_score,axis=1); z['potential_score_v7']=z['quality_score_v8']
    z=z[z.apply(lambda r: passes(r,P2) and extra_pass(r,c),axis=1)].copy()
    z=z.sort_values(['signal_date','symbol','quality_score_v8'],ascending=[True,True,False]).drop_duplicates(['signal_date','symbol'])
    selected=[]; annual={}; last_sym={}
    for dt,g in z.groupby('signal_date',sort=True):
        y=int(pd.Timestamp(dt).year); used=annual.get(y,0)
        if used>=ANNUAL_CAP: continue
        cand=[]
        for _,r in g.sort_values(['quality_score_v8','momentum_12w_pct','momentum_26w_pct'],ascending=False).iterrows():
            prev=last_sym.get(r.symbol)
            if prev is not None and (pd.Timestamp(dt)-prev).days < 7*int(P2['cooldown_weeks']): continue
            cand.append(r)
        if not cand: continue
        pick=pd.DataFrame(cand).head(min(WEEKLY_CAP,ANNUAL_CAP-used))
        if pick.empty: continue
        selected.append(pick); annual[y]=used+len(pick)
        for _,r in pick.iterrows(): last_sym[r.symbol]=pd.Timestamp(dt)
    return pd.concat(selected,ignore_index=True) if selected else z.iloc[0:0].copy()

def trade_metrics(s):
    if s.empty:return {'trades':0,'reward_risk':None,'profit_factor':None,'mean_return_pct':None,'stop_count':0,'stop_rate_pct':None,'tp_d_count':0,'tp_d_rate_pct':None}
    m=summarize_bucket(s,'v10','all'); n=len(s); cats=s.exit_category.astype(str)
    st=int((cats=='PROTECTIVE_STOP').sum()); tp=int((cats=='D_REVERSAL').sum())
    return {'trades':int(n),'reward_risk':m['reward_risk'],'profit_factor':m['profit_factor'],'mean_return_pct':m['mean_return_pct'],'stop_count':st,'stop_rate_pct':round(100*st/n,2),'tp_d_count':tp,'tp_d_rate_pct':round(100*tp/n,2)}

def price_at(bars,sym,dt):
    b=bars.get(sym)
    if b is None or b.empty:return np.nan
    q=b[b.index<=pd.Timestamp(dt)]
    return np.nan if q.empty else float(q.close.iloc[-1])

def portfolio_sim(bars,s):
    if s.empty:return pd.DataFrame(),{}
    s=s.copy(); s['entry_date']=pd.to_datetime(s.entry_date); s['exit_date']=pd.to_datetime(s.exit_date)
    dates=pd.date_range('2024-01-05','2026-12-25',freq='W-FRI')
    cash=INITIAL_CAPITAL; openpos={}; equity=[]
    for dt in dates:
        exits=s[(s.exit_date<=dt)&(s.exit_date>dt-pd.Timedelta(days=7))]
        for i,r in exits.iterrows():
            if i not in openpos: continue
            pos=openpos.pop(i); gross=pos['shares']*float(r.exit_price); cash+=gross*(1-COST_SIDE_PCT/100)
        mtm=0.0
        for pos in openpos.values():
            p=price_at(bars,pos['symbol'],dt)
            if np.isfinite(p): mtm+=pos['shares']*p
        nav_pre=cash+mtm
        entries=s[(s.entry_date<=dt)&(s.entry_date>dt-pd.Timedelta(days=7))]
        for i,r in entries.iterrows():
            if len(openpos)>=MAX_POSITIONS: continue
            alloc=min(nav_pre*TARGET_WEIGHT,cash/(1+COST_SIDE_PCT/100))
            if alloc<=0: continue
            shares=alloc/float(r.entry_price); cash-=alloc*(1+COST_SIDE_PCT/100); openpos[i]={'symbol':r.symbol,'shares':shares}
        mtm=0.0
        for pos in openpos.values():
            p=price_at(bars,pos['symbol'],dt)
            if np.isfinite(p): mtm+=pos['shares']*p
        equity.append({'date':dt,'nav_eur':cash+mtm,'cash_eur':cash,'open_positions':len(openpos)})
    eq=pd.DataFrame(equity); annual=[]
    for y in (2024,2025,2026):
        q=eq[eq.date.dt.year==y]
        if q.empty:continue
        prev=eq[eq.date<q.date.min()]
        start_nav=INITIAL_CAPITAL if prev.empty else float(prev.nav_eur.iloc[-1])
        end_nav=float(q.nav_eur.iloc[-1]); annual.append({'year':y,'start_nav_eur':round(start_nav,2),'end_nav_eur':round(end_nav,2),'net_return_pct':round((end_nav/start_nav-1)*100,3)})
    dd=(eq.nav_eur/eq.nav_eur.cummax()-1)*100
    return eq,{'annual':annual,'max_drawdown_pct':round(float(dd.min()),3),'ending_nav_eur':round(float(eq.nav_eur.iloc[-1]),2),'cumulative_net_return_pct':round(float((eq.nav_eur.iloc[-1]/INITIAL_CAPITAL-1)*100),3)}

def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): raise RuntimeError('lookahead')
    rows=[]; frames=[]; equities=[]; portfolios={}
    for c in CANDIDATES:
        s=select_candidate(df,c); s['candidate']=c['name']; frames.append(s)
        tm=trade_metrics(s); eq,ps=portfolio_sim(bars,s); portfolios[c['name']]=ps
        if not eq.empty: eq['candidate']=c['name']; equities.append(eq)
        vals=[a['net_return_pct'] for a in ps.get('annual',[])]
        rows.append({'candidate':c['name'],**tm,'annual_net_mean_pct':None if not vals else round(float(np.mean(vals)),3),'positive_years':sum(v>0 for v in vals),'bad_years_le_minus5':sum(v<=-5 for v in vals),'max_drawdown_pct':ps.get('max_drawdown_pct'),'cumulative_net_return_pct':ps.get('cumulative_net_return_pct')})
    res=pd.DataFrame(rows); base=res[res.candidate=='BASE_P2'].iloc[0]
    decisions=[]
    for _,r in res.iterrows():
        economic=bool(pd.notna(r.annual_net_mean_pct) and r.annual_net_mean_pct>15 and r.positive_years>=2 and r.bad_years_le_minus5==0)
        rr=bool(pd.notna(r.reward_risk) and r.reward_risk>3.3); pf=bool(pd.notna(r.profit_factor) and r.profit_factor>=1.8)
        risk=bool(r.candidate!='BASE_P2' and pd.notna(r.stop_rate_pct) and r.stop_rate_pct<base.stop_rate_pct)
        decisions.append({'candidate':r.candidate,'economic_gate_net_annual_gt15':economic,'rr_gt_3_3':rr,'pf_ge_1_8':pf,'stop_rate_improves_vs_base':risk,'promotion_candidate':bool(economic and rr and pf and risk),'pf_target_2_met':bool(pd.notna(r.profit_factor) and r.profit_factor>=2.0)})
    errors=[]
    for s in frames:
        if not s.empty:
            if (s.groupby(pd.to_datetime(s.signal_date).dt.year).size()>ANNUAL_CAP).any():errors.append('annual cap exceeded')
            if (s.groupby(pd.to_datetime(s.signal_date)).size()>WEEKLY_CAP).any():errors.append('weekly cap exceeded')
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_SELECTION_PORTFOLIO_V10','generated_at_utc':datetime.now(timezone.utc).isoformat(),'primary_objective':'MEAN_CALENDAR_YEAR_NET_PORTFOLIO_RETURN_GT_15_PCT','secondary_objective':'REWARD_RISK_GT_3_3','portfolio_assumptions':{'initial_capital_eur':INITIAL_CAPITAL,'max_concurrent_positions':MAX_POSITIONS,'target_weight_pct':20.0,'cost_and_slippage_each_side_pct':COST_SIDE_PCT,'annual_entry_cap':ANNUAL_CAP,'weekly_entry_cap':WEEKLY_CAP},'candidates':CANDIDATES,'results':res.to_dict('records'),'portfolio_results':portfolios,'decisions':decisions,'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,'future_signals_not_used_for_current_ranking':True,'outcome_not_used_in_candidate_score':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True,'v9_exact_medians_not_used_as_thresholds':True},'validation_errors':errors,'limitations':['SAME_BANK_RESEARCH_NOT_CLEAN_OOS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FUNDAMENTAL_PIT_YET','PORTFOLIO_COST_ASSUMPTION_NOT_BROKER_SPECIFIC','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    res.to_csv(OUT_CSV,index=False); pd.concat(frames,ignore_index=True).to_csv(OUT_TRADES,index=False); pd.concat(equities,ignore_index=True).to_csv(OUT_EQUITY,index=False)
    lines=['# AT Weekly V10 — Portfolio economics','',f"Status: **{payload['status']}**",'', 'Primary objective: **net annual portfolio return >15%**; secondary objective: **RR >3.3**.','', '| Candidate | Trades | Mean % | RR | PF | Stop % | TP D % | Mean annual net % | Positive years | Max DD % | Cum net % |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in res.to_dict('records'): lines.append(f"| {r['candidate']} | {r['trades']} | {r['mean_return_pct']} | {r['reward_risk']} | {r['profit_factor']} | {r['stop_rate_pct']} | {r['tp_d_rate_pct']} | {r['annual_net_mean_pct']} | {r['positive_years']} | {r['max_drawdown_pct']} | {r['cumulative_net_return_pct']} |")
    lines += ['','## Annual net portfolio returns']
    for name,ps in portfolios.items(): lines.append(f"- **{name}**: "+', '.join(f"{a['year']} {a['net_return_pct']}%" for a in ps.get('annual',[])))
    lines += ['','## Decision gates',json.dumps(decisions,ensure_ascii=False)]
    OUT_MD.write_text('\n'.join(lines)+'\n'); print(json.dumps({'status':payload['status'],'results':res.to_dict('records'),'decisions':decisions,'portfolio_results':portfolios,'validation_errors':errors},indent=2)); return payload

if __name__=='__main__': run()
