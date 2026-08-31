"""Research-only V11: ex-ante theoretical RR + 25/75 staged entry.

Purpose
- Reduce very-early false positives and cash losses without changing locked exits.
- Measure a theoretical reward/risk BEFORE selection using only completed-week data.
- Test a EUR 1,000 / EUR 3,000 equivalent split as 25% probe + 75% confirmation.

No outcome-derived threshold is fitted here. Candidate rules are deliberately coarse.
The locked 9% protective stop and D exit architecture are unchanged.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import numpy as np
import pandas as pd

from .at_weekly_selection_portfolio_v10 import CANDIDATES as V10_CANDIDATES, select_candidate
from .at_weekly_growth_potential_pit_v1 import enrich_trades
from .at_weekly_quarterly_study_v1 import build_universe, trade_ledger, STOP_PCT
from .at_weekly_v1_fixed import _cache_files, _iter_consolidated, CACHE_DIRS

ROOT=Path(__file__).resolve().parents[3]
OUTDIR=ROOT/'outputs/backtest'
OUT_JSON=OUTDIR/'AT_WEEKLY_STAGED_ENTRY_V11.json'
OUT_CSV=OUTDIR/'AT_WEEKLY_STAGED_ENTRY_V11.csv'
OUT_TRADES=OUTDIR/'AT_WEEKLY_STAGED_ENTRY_V11_TRADES.csv'
OUT_EQUITY=OUTDIR/'AT_WEEKLY_STAGED_ENTRY_V11_EQUITY.csv'
OUT_MD=OUTDIR/'AT_WEEKLY_STAGED_ENTRY_V11.md'

INITIAL_CAPITAL=100000.0
MAX_POSITIONS=5
TARGET_WEIGHT=0.20
PROBE_FRAC=0.25
ADD_FRAC=0.75
COST_SIDE_PCT=0.20
NOMINAL_EXAMPLE_EUR=4000.0

BASES=['BREAKOUT30','STRENGTH_DUAL']
RR_FLOORS=[None,3.3]
STAGES=[
 {'name':'FULL_J0','lag_sessions':0},
 {'name':'PROBE_J1','lag_sessions':1},
 {'name':'PROBE_J2','lag_sessions':2},
 {'name':'PROBE_J3','lag_sessions':3},
]

def load_daily():
    out={}
    for path in _cache_files(ROOT/CACHE_DIRS['ACTION']):
        for sym,hist,err in _iter_consolidated(path):
            if sym is None or err or hist is None or hist.empty: continue
            if sym not in out or len(hist)>len(out[sym]): out[sym]=hist
    return out

def exante_features(bars,row):
    b=bars.get(row.symbol)
    if b is None or b.empty:return {'theoretical_target_pct_v11':np.nan,'theoretical_rr_v11':np.nan,'atr20_pct_v11':np.nan,'prior52_range_pct_v11':np.nan}
    sd=pd.Timestamp(row.signal_date); idx=b.index.get_indexer([sd])
    if len(idx)==0 or idx[0]<0:return {'theoretical_target_pct_v11':np.nan,'theoretical_rr_v11':np.nan,'atr20_pct_v11':np.nan,'prior52_range_pct_v11':np.nan}
    i=int(idx[0]); lo=max(0,i-52); prior=b.iloc[lo:i]
    if len(prior)<26:return {'theoretical_target_pct_v11':np.nan,'theoretical_rr_v11':np.nan,'atr20_pct_v11':np.nan,'prior52_range_pct_v11':np.nan}
    ph=float(prior.high.max()); pl=float(prior.low.min()); entry=float(row.entry_price)
    # ATR uses only completed weeks up to signal week.
    bb=b.iloc[max(0,i-20):i+1].copy()
    pc=bb.close.shift(1); tr=pd.concat([(bb.high-bb.low),(bb.high-pc).abs(),(bb.low-pc).abs()],axis=1).max(axis=1)
    atr=float(tr.tail(20).mean()) if len(tr.dropna()) else np.nan
    range_abs=max(0.0,ph-pl)
    # Conservative ex-ante projection: smaller of half the prior 52w range and four weekly ATRs.
    # This is a potential projection from entry, not a reconstructed analyst target.
    potential_abs=min(0.5*range_abs,4.0*atr) if np.isfinite(atr) else 0.5*range_abs
    target_pct=100.0*potential_abs/entry if entry>0 else np.nan
    rr=target_pct/STOP_PCT if np.isfinite(target_pct) else np.nan
    return {'theoretical_target_pct_v11':round(float(target_pct),3),'theoretical_rr_v11':round(float(rr),3),
            'atr20_pct_v11':round(float(100*atr/entry),3) if np.isfinite(atr) and entry>0 else np.nan,
            'prior52_range_pct_v11':round(float(100*range_abs/entry),3) if entry>0 else np.nan}

def entry_sessions(hist,entry_week_end):
    end=pd.Timestamp(entry_week_end); start=end-pd.Timedelta(days=6)
    q=hist[(hist.index>=start)&(hist.index<=end)]
    return q.sort_index()

def strategic_exit_session(hist,exit_week_end):
    end=pd.Timestamp(exit_week_end); start=end-pd.Timedelta(days=6)
    q=hist[(hist.index>=start)&(hist.index<=end)]
    return None if q.empty else pd.Timestamp(q.index[0])

def protective_exit_session(hist,entry_week_end,exit_week_end,stop_price):
    start=pd.Timestamp(entry_week_end)-pd.Timedelta(days=6); end=pd.Timestamp(exit_week_end)
    q=hist[(hist.index>=start)&(hist.index<=end)].sort_index()
    for dt,r in q.iterrows():
        if float(r.open)<=stop_price or float(r.low)<=stop_price:return pd.Timestamp(dt)
    return None

def prepare_trade(daily,row,lag):
    hist=daily.get(row.symbol)
    if hist is None or hist.empty:return None
    es=entry_sessions(hist,row.entry_date)
    if es.empty:return None
    entry_dt=pd.Timestamp(es.index[0]); entry_px=float(row.entry_price); stop_px=entry_px*(1-STOP_PCT/100)
    if str(row.exit_category)=='PROTECTIVE_STOP':
        exit_dt=protective_exit_session(hist,row.entry_date,row.exit_date,stop_px)
        intraday_stop=True
    else:
        exit_dt=strategic_exit_session(hist,row.exit_date); intraday_stop=False
    if exit_dt is None:return None
    exit_px=float(row.exit_price)
    add_dt=None; add_px=None; confirmed=False
    if lag>0:
        after=hist[hist.index>=entry_dt].sort_index()
        # confirmation is observed after lag completed sessions; add at the next session open.
        if len(after)>=lag+1:
            conf=after.iloc[lag-1]; nxt=after.iloc[lag]; nxt_dt=pd.Timestamp(after.index[lag])
            prev_close=float(after.iloc[lag-2].close) if lag>=2 else entry_px
            no_stop=True
            pre=after.iloc[:lag]
            for _,x in pre.iterrows():
                if float(x.open)<=stop_px or float(x.low)<=stop_px: no_stop=False; break
            confirmed=bool(no_stop and float(conf.close)>entry_px and (lag==1 or float(conf.close)>prev_close))
            # never add after/equal an already-known exit session.
            if confirmed and nxt_dt<exit_dt:
                add_dt=nxt_dt; add_px=float(nxt.open)
    return {'entry_dt':entry_dt,'entry_px':entry_px,'stop_px':stop_px,'add_dt':add_dt,'add_px':add_px,
            'exit_dt':exit_dt,'exit_px':exit_px,'intraday_stop':intraday_stop,'confirmed':confirmed}

def fixed_nominal_return(prep,lag):
    buy1=NOMINAL_EXAMPLE_EUR if lag==0 else NOMINAL_EXAMPLE_EUR*PROBE_FRAC
    sh=buy1/prep['entry_px']; costs=buy1*COST_SIDE_PCT/100
    invested=buy1
    if lag>0 and prep['add_dt'] is not None:
        buy2=NOMINAL_EXAMPLE_EUR*ADD_FRAC; sh+=buy2/prep['add_px']; invested+=buy2; costs+=buy2*COST_SIDE_PCT/100
    proceeds=sh*prep['exit_px']; costs+=proceeds*COST_SIDE_PCT/100
    pnl=proceeds-invested-costs
    return 100*pnl/NOMINAL_EXAMPLE_EUR,pnl,invested

def build_events(rows,daily,lag):
    events=[]; audit=[]
    for k,r in rows.reset_index(drop=True).iterrows():
        p=prepare_trade(daily,r,lag)
        if p is None: continue
        rr,pnl,invested=fixed_nominal_return(p,lag)
        audit.append({**dict(r),'stage_lag_sessions':lag,'probe_confirmed':p['confirmed'],'add_date':None if p['add_dt'] is None else p['add_dt'].date().isoformat(),
                      'add_price':p['add_px'],'actual_exit_session':p['exit_dt'].date().isoformat(),'staged_return_pct_on_planned_4000':round(rr,3),
                      'staged_pnl_eur_on_planned_4000':round(pnl,2),'actual_invested_eur_example':round(invested,2)})
        tid=f"{r.symbol}|{pd.Timestamp(r.entry_date).date()}|{k}"
        events.append({'date':p['entry_dt'],'order':1,'type':'ENTRY','tid':tid,'symbol':r.symbol,'price':p['entry_px']})
        if lag>0 and p['add_dt'] is not None: events.append({'date':p['add_dt'],'order':1,'type':'ADD','tid':tid,'symbol':r.symbol,'price':p['add_px']})
        order=2 if p['intraday_stop'] and p['exit_dt']==p['entry_dt'] else 0
        events.append({'date':p['exit_dt'],'order':order,'type':'EXIT','tid':tid,'symbol':r.symbol,'price':p['exit_px']})
    return pd.DataFrame(events),pd.DataFrame(audit)

def portfolio_sim(rows,daily,lag):
    ev,audit=build_events(rows,daily,lag)
    if ev.empty:return {},pd.DataFrame(),audit
    dates=sorted(set(pd.Timestamp(x) for h in daily.values() for x in h.index if pd.Timestamp('2024-01-01')<=pd.Timestamp(x)<=pd.Timestamp('2026-12-31')))
    bydate={d:g.sort_values('order') for d,g in ev.groupby('date')}
    cash=INITIAL_CAPITAL; pos={}; eq=[]; costs=0.0
    for dt in dates:
        dayev=bydate.get(dt)
        if dayev is not None:
            # strategic exits (order 0), then entry/add, then same-day protective exit (order 2)
            for _,e in dayev.iterrows():
                typ=e['type']; tid=e['tid']; px=float(e['price'])
                if typ=='EXIT':
                    if tid not in pos: continue
                    gross=pos[tid]['shares']*px; fee=gross*COST_SIDE_PCT/100; costs+=fee; cash+=gross-fee; pos.pop(tid,None)
                elif typ=='ENTRY':
                    if len(pos)>=MAX_POSITIONS: continue
                    # plan 20% NAV, but expose only 25% initially for staged policies.
                    mtm=sum(v['shares']*float(daily[v['symbol']].loc[:dt].close.iloc[-1]) for v in pos.values() if not daily[v['symbol']].loc[:dt].empty)
                    nav=cash+mtm; planned=nav*TARGET_WEIGHT; amount=planned if lag==0 else planned*PROBE_FRAC
                    amount=min(amount,cash/(1+COST_SIDE_PCT/100))
                    if amount<=0: continue
                    fee=amount*COST_SIDE_PCT/100; costs+=fee; cash-=amount+fee
                    pos[tid]={'symbol':e['symbol'],'shares':amount/px,'planned':planned,'added':lag==0}
                elif typ=='ADD':
                    if tid not in pos or pos[tid]['added']: continue
                    amount=min(pos[tid]['planned']*ADD_FRAC,cash/(1+COST_SIDE_PCT/100))
                    if amount<=0: continue
                    fee=amount*COST_SIDE_PCT/100; costs+=fee; cash-=amount+fee
                    pos[tid]['shares']+=amount/px; pos[tid]['added']=True
        mtm=0.0
        for v in pos.values():
            h=daily.get(v['symbol']); q=h[h.index<=dt] if h is not None else pd.DataFrame()
            if not q.empty: mtm+=v['shares']*float(q.close.iloc[-1])
        eq.append({'date':dt,'nav_eur':cash+mtm,'cash_eur':cash,'open_positions':len(pos)})
    e=pd.DataFrame(eq); peak=e.nav_eur.cummax(); dd=(e.nav_eur/peak-1)*100
    annual=[]
    for y in [2024,2025,2026]:
        q=e[e.date.dt.year==y]
        if q.empty: continue
        prior=e[e.date<q.date.min()]
        s=INITIAL_CAPITAL if prior.empty else float(prior.nav_eur.iloc[-1]); z=float(q.nav_eur.iloc[-1])
        annual.append({'year':y,'net_return_pct':round(100*(z/s-1),3),'start_nav_eur':round(s,2),'end_nav_eur':round(z,2)})
    stats={'annual':annual,'annual_net_mean_pct':round(float(np.mean([x['net_return_pct'] for x in annual])),3) if annual else None,
           'positive_years':sum(x['net_return_pct']>0 for x in annual),'ending_nav_eur':round(float(e.nav_eur.iloc[-1]),2),
           'cumulative_net_return_pct':round(100*(float(e.nav_eur.iloc[-1])/INITIAL_CAPITAL-1),3),'max_drawdown_pct':round(float(dd.min()),3),
           'total_transaction_cost_eur':round(costs,2)}
    return stats,e,audit

def metric_from_audit(a):
    if a.empty:return {'trades':0,'rr':None,'pf':None,'mean_trade_return_pct':None,'loss_trades':0,'avg_loss_pct':None,'max_loss_pct':None,'probe_only_trades':0}
    x=pd.to_numeric(a.staged_return_pct_on_planned_4000,errors='coerce').dropna(); w=x[x>0]; l=x[x<0]
    rr=None if w.empty or l.empty else float(w.mean()/abs(l.mean())); pf=None if l.empty or -l.sum()<=0 else float(w.sum()/(-l.sum()))
    return {'trades':int(len(x)),'rr':None if rr is None else round(rr,3),'pf':None if pf is None else round(pf,3),'mean_trade_return_pct':round(float(x.mean()),3),
            'loss_trades':int((x<0).sum()),'avg_loss_pct':None if l.empty else round(float(l.mean()),3),'max_loss_pct':None if l.empty else round(float(l.min()),3),
            'probe_only_trades':int(a.add_date.isna().sum())}

def run():
    bars,arr,sigs,first,last=build_universe(); ledger=trade_ledger(bars,arr,sigs)
    df=enrich_trades(bars,ledger); df=df[~df.endpoint_mark.astype(bool)].copy()
    for c in ['theoretical_target_pct_v11','theoretical_rr_v11','atr20_pct_v11','prior52_range_pct_v11']: df[c]=np.nan
    for i,r in df.iterrows():
        f=exante_features(bars,r)
        for k,v in f.items(): df.at[i,k]=v
    daily=load_daily(); results=[]; all_audit=[]; all_eq=[]; portfolios={}
    errors=[]
    if (pd.to_datetime(df.feature_timestamp)>pd.to_datetime(df.signal_date)).any():errors.append('lookahead feature timestamp')
    v10map={c['name']:c for c in V10_CANDIDATES}
    for base in BASES:
        selected=select_candidate(df,v10map[base]).copy()
        for floor in RR_FLOORS:
            q=selected.copy()
            label='NO_RR_FLOOR' if floor is None else f'RR_GE_{str(floor).replace(".","_")}'
            if floor is not None:q=q[pd.to_numeric(q.theoretical_rr_v11,errors='coerce')>=floor].copy()
            for st in STAGES:
                lag=st['lag_sessions']; stats,eq,audit=portfolio_sim(q,daily,lag)
                m=metric_from_audit(audit); key=f'{base}|{label}|{st["name"]}'
                portfolios[key]=stats
                row={'configuration':key,'base':base,'rr_floor':floor,'stage':st['name'],**m,**stats}
                results.append(row)
                if not audit.empty:
                    audit['configuration']=key; all_audit.append(audit)
                if not eq.empty:
                    eq['configuration']=key; all_eq.append(eq)
    res=pd.DataFrame(results)
    # Promotion is intentionally demanding and does not rely on a single aggregate number.
    res['gate_rr_gt_3_3']=pd.to_numeric(res.rr,errors='coerce')>3.3
    res['gate_annual_net_gt15']=pd.to_numeric(res.annual_net_mean_pct,errors='coerce')>15
    res['gate_positive_years_ge2']=pd.to_numeric(res.positive_years,errors='coerce')>=2
    res['gate_dd_better_minus20']=pd.to_numeric(res.max_drawdown_pct,errors='coerce')>-20
    res['promotion_candidate']=res[['gate_rr_gt_3_3','gate_annual_net_gt15','gate_positive_years_ge2','gate_dd_better_minus20']].all(axis=1)
    payload={'status':'SUCCESS' if not errors else 'VALIDATION_FAILED','version':'AT_WEEKLY_STAGED_ENTRY_V11','generated_at_utc':datetime.now(timezone.utc).isoformat(),
             'objective':{'primary':'NET_ANNUAL_PORTFOLIO_RETURN_GT_15_PCT','secondary':'RR_GT_3_3','loss_reduction':'EARLY_PROBE_25_PCT_THEN_ADD_75_PCT_ON_CONFIRMATION'},
             'nominal_example':{'day0_eur':1000,'conditional_add_eur':3000,'planned_total_eur':4000},
             'portfolio_assumptions':{'initial_capital_eur':INITIAL_CAPITAL,'max_positions':MAX_POSITIONS,'planned_weight_per_trade_pct':TARGET_WEIGHT*100,'probe_fraction_pct':25,'add_fraction_pct':75,'cost_slippage_each_side_pct':COST_SIDE_PCT},
             'theoretical_rr_formula':'min(0.5*prior_52w_high_low_range,4*weekly_ATR20)/(9%*entry_price)',
             'confirmation_rule':'after N completed daily sessions: close>entry and, for N>=2, close>previous close; no stop touch; add next session open',
             'results':res.replace({np.nan:None}).to_dict('records'),'lookahead_controls':{'completed_week_features_only_for_exante_rr':True,'prior52_ends_at_signal':True,'daily_confirmation_uses_completed_sessions_only':True,'add_executes_next_session_open':True,'future_trade_outcome_not_used_for_confirmation':True,'locked_trade_ledger_reused':True,'locked_entry_exit_unchanged':True},
             'validation_errors':errors,'limitations':['SAME_BANK_RESEARCH_NOT_CLEAN_OOS','SMALL_SAMPLE','CURRENT_CACHE_UNIVERSE_NOT_PIT_MEMBERSHIP','SURVIVORSHIP_BIAS_POSSIBLE','THEORETICAL_TARGET_IS_TECHNICAL_PROJECTION_NOT_ANALYST_CONSENSUS','COST_MODEL_NOT_BROKER_SPECIFIC','RESEARCH_ONLY']}
    OUTDIR.mkdir(parents=True,exist_ok=True); OUT_JSON.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    res.to_csv(OUT_CSV,index=False)
    pd.concat(all_audit,ignore_index=True).to_csv(OUT_TRADES,index=False) if all_audit else pd.DataFrame().to_csv(OUT_TRADES,index=False)
    pd.concat(all_eq,ignore_index=True).to_csv(OUT_EQUITY,index=False) if all_eq else pd.DataFrame().to_csv(OUT_EQUITY,index=False)
    lines=['# AT Weekly V11 — staged entry + ex-ante theoretical RR','',f"Status: **{payload['status']}**",'',
           'Probe model: 25% at J, remaining 75% only after daily confirmation. EUR 1,000 + EUR 3,000 is the concrete 4,000 EUR example.','',
           '| Configuration | Trades | RR | PF | Mean trade % | Losses | Avg loss % | Max loss % | Probe-only | Mean annual net % | Positive years | Max DD % | Cum net % | Promote |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|']
    for r in res.to_dict('records'):
        lines.append(f"| {r['configuration']} | {r['trades']} | {r['rr']} | {r['pf']} | {r['mean_trade_return_pct']} | {r['loss_trades']} | {r['avg_loss_pct']} | {r['max_loss_pct']} | {r['probe_only_trades']} | {r.get('annual_net_mean_pct')} | {r.get('positive_years')} | {r.get('max_drawdown_pct')} | {r.get('cumulative_net_return_pct')} | {r['promotion_candidate']} |")
    OUT_MD.write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':payload['status'],'top':res.sort_values(['promotion_candidate','annual_net_mean_pct','rr'],ascending=False).head(8).replace({np.nan:None}).to_dict('records'),'validation_errors':errors},indent=2))
    return payload

if __name__=='__main__':run()
