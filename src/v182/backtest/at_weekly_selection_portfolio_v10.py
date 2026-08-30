"""Research-only V10: pre-declared quality filters + real portfolio economics.

Primary objective: >15% annual net portfolio return. Secondary objective: RR >3.3.
This stage preserves the locked 9% protective stop and locked exit architecture.
It uses only completed-week PIT technical features already available in the research bank.
No historical analyst consensus or fundamentals are fabricated.

Portfolio assumptions (pre-declared):
- initial capital EUR 100,000
- maximum 5 concurrent positions
- target allocation 20% of current NAV per new position
- maximum 30 entries per calendar year, maximum 1 per signal week
- transaction cost + slippage: 0.20% each side (0.40% round trip)
- year-end NAV includes marked-to-market open positions using available weekly closes

Candidate family is intentionally small and coarse, motivated by V9 diagnostics but not
fit to exact outcome medians:
- BASE_P2: frozen V8 P2 (MOM12>=40, quality score>=88, 12w symbol cooldown)
- BREAKOUT30: P2 + breakout above prior 52w high >=30%
- MOM26_150: P2 + 26w momentum >=150%
- STRENGTH_DUAL: P2 + breakout>=30% + 26w momentum>=150%
- RSI80: P2 + RSI>=80

Promotion requires ALL of:
- RR > 3.3
- profit factor >= 1.8 (2.0 target reported separately)
- stop rate < frozen P2 baseline stop rate
- at least 2 evaluable calendar years with positive net portfolio return
- mean evaluable annual net return > 15%
- no evaluable annual return <= -5%
The >15% annual objective is therefore an economic gate, not a cosmetic statistic.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_selection_quality_v8_top30 import P2 if False else select  # noqa: F401
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
 {'name':'MOM26_150','m26_min_v10':150.0},
 {'name':'STRENGTH_DUAL','breakout_min':30.0,'m26_min_v10':150.0},
 {'name':'RSI80','rsi_min_v10':80.0},
]

def finite_ge(r,k,v):
    x=r.get(k,np.nan)
    return bool(np.isfinite(x) and float(x)>=float(v))

def extra_pass(r,c):
    if 'breakout_min' in c and not finite_ge(r,'breakout_above_prior_52w_high_pct',c['breakout_min']): return False
    if 'm26_min_v10' in c and not finite_ge(r,'momentum_26w_pct',c['m26_min_v10']): return False
    if 'rsi_min_v10' in c and not finite_ge(r,'rsi14',c['rsi_min_v10']): return False
    return True

def select_candidate(df,c):
    z=df.copy(); z['signal_date']=pd.to_datetime(z.signal_date)
    z['quality_score_v8']=z.apply(potential_score,axis=1)
    z['potential_score_v7']=z['quality_score_v8']
    z=z[z.apply(lambda r: passes(r,P2) and extra_pass(r,c),axis=1)].copy()
    z=z.sort_values(['signal_date','symbol','quality_score_v8'],ascending=[True,True,False]).drop_duplicates(['signal_date','symbol'])
    selected=[]; annual={}; last_sym={}
    for dt,g in z.groupby('signal_date',sort=True):
        year=int(pd.Timestamp(dt).year); used=annual.get(year,0)
        if used>=ANNUAL_CAP: continue
        cand=[]
        for _,r in g.sort_values(['quality_score_v8','momentum_12w_pct','momentum_26w_pct'],ascending=False).iterrows():
            prev=last_sym.get(r.symbol)
            if prev is not None and (pd.Timestamp(dt)-prev).days < 7*int(P2['cooldown_weeks']): continue
            cand.append(r)
        if not cand: continue
        pick=pd.DataFrame(cand).head(min(WEEKLY_CAP,ANNUAL_CAP-used))
        if pick.empty: continue
        selected.append(pick); annual[year]=used+len(pick)
        for _,r in pick.iterrows(): last_sym[r.symbol]=pd.Timestamp(dt)
    return pd.concat(selected,ignore_index=True) if selected else z.iloc[0:0].copy()

def trade_metrics(s):
    if s.empty:
        return {'trades':0,'win_rate_pct':None,'mean_return_pct':None,'reward_risk':None,'profit_factor':None,'p10_return_pct':None,'stop_count':0,'stop_rate_pct':None,'tp_d_count':0,'tp_d_rate_pct':None}
    m=summarize_bucket(s,'v10','all')
    n=len(s); cats=s.exit_category.astype(str)
    stops=int((cats=='PROTECTIVE_STOP').sum()); tp=int((cats=='D_REVERSAL').sum())
    return {'trades':int(n),'win_rate_pct':m['win_rate_pct'],'mean_return_pct':m['mean_return_pct'],'reward_risk':m['reward_risk'],'profit_factor':m['profit_factor'],'p10_return_pct':m['p10_return_pct'],'stop_count':stops,'stop_rate_pct':round(100*stops/n,2),'tp_d_count':tp,'tp_d_rate_pct':round(100*tp/n,2)}

def px_close(bars,symbol,dt):
    b=bars.get(symbol)
    if b is None or b.empty: return np.nan
    d=pd.Timestamp(dt)
    sub=b[b.index<=d]
    if sub.empty:return np.nan
    return float(sub.close.iloc[-1])

def portfolio_sim(bars,s):
    if s.empty:return pd.DataFrame(),pd.DataFrame(),{}
    s=s.copy(); s['entry_date']=pd.to_datetime(s.entry_date); s['exit_date']=pd.to_datetime(s.exit_date)
    start=min(pd.Timestamp('2024-01-01'),s.entry_date.min())
    end=max(pd.Timestamp('2026-12-31'),s.exit_date.max())
    dates=pd.date_range(start,end,freq='W-FRI')
    cash=INITIAL_CAPITAL; openpos={}; events=[]; equity=[]
    entries={d:list(g.index) for d,g in s.groupby('entry_date')}
    exits={d:list(g.index) for d,g in s.groupby('exit_date')}
    for dt in dates:
        # exits known for this week: settle first to free cash
        for ed,idxs in list(exits.items()):
            if ed>dt or ed<=dt-pd.Timedelta(days=7): continue
            for i in idxs:
                if i not in openpos: continue
                r=s.loc[i]; pos=openpos.pop(i)
                gross=pos['shares']*float(r.exit_price)
                fee=gross*(COST_SIDE_PCT/100.0); cash+=gross-fee
                events.append({'date':ed,'type':'EXIT','candidate':r.get('candidate',''),'symbol':r.symbol,'gross_eur':gross,'cost_eur':fee,'cash_after':cash})
        # mark NAV before entries
        mtm=0.0
        for i,pos in openpos.items():
            p=px_close(bars,pos['symbol'],dt)
            if np.isfinite(p): mtm+=pos['shares']*p
        nav_pre=cash+mtm
        # entries falling in this week, ranking already max1/week
        for ed,idxs in list(entries.items()):
            if ed>dt or ed<=dt-pd.Timedelta(days=7): continue
            for i in idxs:
                if len(openpos)>=MAX_POSITIONS: continue
                r=s.loc[i]
                target=min(nav_pre*TARGET_WEIGHT,cash/(1+COST_SIDE_PCT/100.0))
                if target<=0: continue
                shares=target/float(r.entry_price); fee=target*(COST_SIDE_PCT/100.0)
                cash-=target+fee
                openpos[i]={'symbol':r.symbol,'shares':shares,'entry_price':float(r.entry_price)}
                events.append({'date':ed,'type':'ENTRY','candidate':r.get('candidate',''),'symbol':r.symbol,'gross_eur':target,'cost_eur':fee,'cash_after':cash})
        mtm=0.0
        for i,pos in openpos.items():
            p=px_close(bars,pos['symbol'],dt)
            if np.isfinite(p): mtm+=pos['shares']*p
        nav=cash+mtm
        equity.append({'date':dt,'nav_eur':nav,'cash_eur':cash,'open_positions':len(openpos)})
    eq=pd.DataFrame(equity); ev=pd.DataFrame(events)
    annual=[]
    for y in [2024,2025,2026]:
        q=eq[eq.date.dt.year==y]
        if q.empty: continue
        start_nav=INITIAL_CAPITAL if y==2024 else float(eq[eq.date<q.date.min()].nav_eur.iloc[-1]) if not eq[eq.date<q.date.min()].empty else INITIAL_CAPITAL
        end_nav=float(q.nav_eur.iloc[-1]); ret=(end_nav/start_nav-1)*100
        annual.append({'year':y,'start_nav_eur':round(start_nav,2),'end_nav_eur':round(end_nav,2),'net_return_pct':round(ret,3)})
    peak=eq.nav_eur.cummax(); dd=(eq.nav_eur/peak-1)*100
    stats={'max_drawdown_pct':round(float(dd.min()),3),'ending_nav_eur':round(float(eq.nav_eur.iloc[-1]),2),'cumulative_net_return_pct':round(float((eq.nav_eur.iloc[-1]/INITIAL_CAPITAL-1)*100),3),'annual':annual,'cost_side_pct':COST_SIDE_PCT,'initial_capital_eur':INITIAL_CAPITAL,'target_weight_pct':TARGET_WEIGHT*100,'max_positions':MAX_POSITIONS}
    return eq,ev,stats

def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any(): raise RuntimeError('lookahead feature timestamp')
    rows=[]; frames=[]; equities=[]; portfolios={}
    for c in CANDIDATES:
        s=select_candidate(df,c); s['candidate']=c['name']; frames.append(s)
        tm=trade_metrics(s); eq,ev,ps=portfolio_sim(bars,s); portfolios[c['name']]=ps
        if not eq.empty: eq['candidate']=c['name']; equities.append(eq)
        annual=ps.get('annual',[])
        evalyrs=[a for a in annual if a['year'] in (2024,2025,2026)]
        annmean=float(np.mean([a['net_return_pct'] for a in evalyrs])) if evalyrs else np.nan
        posyrs=sum(a['net_return_pct']>0 for a in evalyrs); badyrs=sum(a['net_return_pct']<=-5 for a in evalyrs)
        rows.append({'candidate':c['name'],**tm,'annual_net_mean_pct':None if not np.isfinite(annmean) else round(annmean,3),'positive_years':posyrs,'bad_years_le_minus5':badyrs,'max_drawdown_pct':ps.get('max_drawdown_pct'),'cumulative_net_return_pct':ps.get('cumulative_net_return_pct')})
    res=pd.DataFrame(rows)
    base=res[res.candidate=='BASE_P2'].iloc[0]
    decisions=[]
    for _,r in res.iterrows():
        ps=portfolios[r.candidate]; annual=ps.get('annual',[]); evalyrs=annual
        economic=bool(len(evalyrs)>=2 and r.annual_net_mean_pct is not None and float(r.annual_net_mean_pct)>15.0 and r.positive_years>=2 and r.bad_years_le_minus5==0)
        rr=bool(pd.notna(r.reward_risk) and float(r.reward_risk)>3.3)
        pf=bool(pd.notna(r.profit_factor) and float(r.profit_factor)>=1.8)
        risk=bool(pd.notna(r.stop_rate_pct) and float(r.stop_rate_pct)<float(base.stop_rate_pct)) if r.candidate!='BASE_P2' else False
        decisions.append({'candidate':r.candidate,'economic_gate_net_annual_gt15':economic,'rr_gt_3_3':rr,'pf_ge_1_8':pf,'stop_rate_improves_vs_base':risk,'promotion_candidate':bool(economic and rr and pf and risk),'pf_target_2_met':bool(pd.notna(r.profit_factor) and float(r.profit_factor)>=2.0)})
    errors=[]
    for s in frames:
        if not s.empty:
            if (s.groupby(pd.to_datetime(s.signal_date).dt.year).size()>ANNUAL_CAP).any(): errors.append('annual cap exceeded')
            if (s.groupby(pd.to_datetime(s.signal_date)).size()>WEEKLY_CAP).any(): errors.append('weekly cap exceeded')
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_SELECTION_PORTFOLIO_V10','generated_at_utc':datetime.now(timezone.utc).isoformat(),'primary_objective':'MEAN_CALENDAR_YEAR_NET_PORTFOLIO_RETURN_GT_15_PCT','secondary_objective':'REWARD_RISK_GT_3_3','portfolio_assumptions':{'initial_capital_eur':INITIAL_CAPITAL,'max_concurrent_positions':MAX_POSITIONS,'target_weight_pct':TARGET_WEIGHT*100,'cost_and_slippage_each_side_pct':COST_SIDE_PCT,'annual_entry_cap':ANNUAL_CAP,'weekly_entry_cap':WEEKLY_CAP},'candidates':CANDIDATES,'results':res.to_dict('records'),'portfolio_results':portfolios,'decisions':decisions,'lookahead_controls':{'completed_week_features_only':True,'feature_timestamp_le_signal':True,'future_signals_not_used_for_current_ranking':True,'outcome_not_used_in_candidate_score':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True,'v9_exact_medians_not_used_as_thresholds':True},'validation_errors':errors,'limitations':['SAME_BANK_RESEARCH_NOT_CLEAN_OOS','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','NO_FUNDAMENTAL_PIT_YET','PORTFOLIO_COST_ASSUMPTION_NOT_BROKER_SPECIFIC','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    res.to_csv(OUT_CSV,index=False); pd.concat(frames,ignore_index=True).to_csv(OUT_TRADES,index=False); pd.concat(equities,ignore_index=True).to_csv(OUT_EQUITY,index=False)
    lines=['# AT Weekly V10 — Portfolio economics','',f"Status: **{payload['status']}**",'',f"Primary objective: **net annual portfolio return >15%**; secondary objective: **RR >3.3**.",'', '| Candidate | Trades | Mean % | RR | PF | Stop % | TP D % | Mean annual net % | Positive years | Max DD % | Cum net % |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in res.to_dict('records'):
        lines.append(f"| {r['candidate']} | {r['trades']} | {r['mean_return_pct']} | {r['reward_risk']} | {r['profit_factor']} | {r['stop_rate_pct']} | {r['tp_d_rate_pct']} | {r['annual_net_mean_pct']} | {r['positive_years']} | {r['max_drawdown_pct']} | {r['cumulative_net_return_pct']} |")
    lines += ['','## Annual net portfolio returns']
    for name,ps in portfolios.items():
        lines.append(f"- **{name}**: "+', '.join(f"{a['year']} {a['net_return_pct']}%" for a in ps.get('annual',[])))
    lines += ['','## Decision gates',json.dumps(decisions,ensure_ascii=False)]
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'results':res.to_dict('records'),'decisions':decisions,'portfolio_results':portfolios,'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__': run()
