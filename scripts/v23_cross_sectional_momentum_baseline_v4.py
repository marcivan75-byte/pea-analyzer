from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

INITIAL_CAPITAL=65000.0
FEE=0.002
STRESS_SLIP=0.001
CUTOFF=pd.Timestamp('2023-01-01')
TOP_N=10
LOOKBACK=252
SKIP=21
MIN_OBS=253
MAX_LEG_DELAY_CAL_DAYS=10


def norm(df):
    m={str(c).strip().lower():c for c in df.columns}
    def pick(*xs):
        for x in xs:
            if x in m:return m[x]
        raise SystemExit(f'BLOCK_SCHEMA missing {xs}')
    z=pd.DataFrame({'isin':df[pick('isin')].astype(str),
                    'date':pd.to_datetime(df[pick('date','datetime')],errors='coerce').dt.tz_localize(None).dt.normalize(),
                    'close':pd.to_numeric(df[pick('close','adj_close','adjusted_close')],errors='coerce')})
    z=z.dropna().query('close>0').copy()
    z=z[z['date']<CUTOFF].drop_duplicates(['isin','date'],keep='last').sort_values(['isin','date'])
    if z.empty or z['date'].max()>=CUTOFF:raise SystemExit('BLOCK_GOVERNANCE')
    return z


def build_signals(z):
    parts=[]
    for isin,g in z.groupby('isin',sort=False):
        g=g.sort_values('date').copy(); g['mom']=g.close.shift(SKIP)/g.close.shift(LOOKBACK)-1.0
        g['obs']=np.arange(len(g))+1; g['month']=g.date.dt.to_period('M')
        parts.append(g[['isin','date','close','mom','obs','month']])
    x=pd.concat(parts,ignore_index=True)
    anchors=x.groupby('month',as_index=False).date.max().rename(columns={'date':'signal_date'})
    last=x.sort_values(['isin','month','date']).groupby(['isin','month'],as_index=False).tail(1)
    s=last.merge(anchors,on='month',how='left')
    s=s[(s.obs>=MIN_OBS)&s.mom.notna()&(s.date<=s.signal_date)].copy()
    s['rank']=s.groupby('signal_date').mom.rank(method='first',ascending=False)
    s=s[s['rank']<=TOP_N].sort_values(['signal_date','rank','isin'])
    return s[['signal_date','date','isin','close','mom','rank']].rename(columns={'date':'feature_date'})


def first_quote_after(byisin, isin, after_date):
    g=byisin.get(isin)
    if g is None:return None
    idx=g.index[g.index>after_date]
    return pd.Timestamp(idx[0]) if len(idx) else None


def metrics(eq,tr,mode,signal_to_complete):
    peak=eq.equity_eur.cummax(); dd=eq.equity_eur/peak-1
    daily=eq.set_index('date').equity_eur.pct_change().dropna()
    years=max((eq.date.iloc[-1]-eq.date.iloc[0]).days/365.2425,1e-9); final=float(eq.equity_eur.iloc[-1])
    delays=list(signal_to_complete.values())
    return {'mode':mode,'start':str(eq.date.iloc[0].date()),'end':str(eq.date.iloc[-1].date()),
            'initial_capital_eur':INITIAL_CAPITAL,'final_equity_eur':final,'net_eur':final-INITIAL_CAPITAL,
            'net_return':final/INITIAL_CAPITAL-1,'cagr':(final/INITIAL_CAPITAL)**(1/years)-1,
            'max_drawdown':float(dd.min()),'annualized_volatility':float(daily.std(ddof=1)*np.sqrt(252)),
            'fees_eur':float(tr.fee.sum()) if len(tr) else 0.0,'trade_actions':int(len(tr)),
            'avg_open_positions':float(eq.open_positions.mean()),'max_open_positions':int(eq.open_positions.max()),
            'completed_rebalances':int(len(delays)),'max_signal_to_complete_calendar_days':int(max(delays) if delays else 0),
            'mean_signal_to_complete_calendar_days':float(np.mean(delays) if delays else 0.0),
            'top_n':TOP_N,'lookback_days':LOOKBACK,'skip_days':SKIP,'variant_count':1}


def simulate(z,signals,stress=False):
    slip=STRESS_SLIP if stress else 0.0
    dates=pd.DatetimeIndex(sorted(z.date.unique()))
    byisin={i:g.set_index('date')['close'].sort_index() for i,g in z.groupby('isin')}
    bydate={pd.Timestamp(d):g.set_index('isin').close.to_dict() for d,g in z.groupby('date')}
    targets={pd.Timestamp(d):g.sort_values('rank')['isin'].tolist() for d,g in signals.groupby('signal_date')}
    cash=INITIAL_CAPITAL; hold={}; lastpx={}; trades=[]; eqrows=[]; signal_to_complete={}
    event_queue={}; active_rebalance=None

    def mark():return cash+sum(q*lastpx[i] for i,q in hold.items() if i in lastpx)
    def enqueue(d,event):event_queue.setdefault(pd.Timestamp(d),[]).append(event)

    for d in dates:
        d=pd.Timestamp(d); pxs=bydate[d]; lastpx.update({i:float(p) for i,p in pxs.items()})
        due=event_queue.pop(d,[])
        for ev in due:
            typ=ev['type']; i=ev['isin']; sd=ev['signal_date']; raw=float(pxs[i])
            if typ=='SELL':
                q=hold.pop(i,0)
                if q>0:
                    px=raw*(1-slip); fee=q*px*FEE; cash+=q*px-fee
                    trades.append({'date':d,'signal_date':sd,'isin':i,'side':'SELL_REBAL','qty':q,'px':px,'fee':fee})
                active_rebalance['sells_remaining'].discard(i)
                if not active_rebalance['sells_remaining'] and not active_rebalance.get('buys_scheduled',False):
                    ready=d; target_value=cash/TOP_N; active_rebalance['buys_scheduled']=True
                    for j in active_rebalance['targets']:
                        bd=first_quote_after(byisin,j,ready)
                        if bd is None or (bd-ready).days>MAX_LEG_DELAY_CAL_DAYS:
                            raise SystemExit(f'BLOCK_BUY_DELAY {sd.date()} {j} ready={ready.date()} buy={bd}')
                        enqueue(bd,{'type':'BUY','isin':j,'signal_date':sd,'target_value':target_value})
            elif typ=='BUY':
                px=raw*(1+slip); tv=float(ev['target_value']); q=int(np.floor(tv/(px*(1+FEE))))
                fee=q*px*FEE; cost=q*px+fee
                if q>0 and cost<=cash+1e-8:
                    cash-=cost; hold[i]=q
                    trades.append({'date':d,'signal_date':sd,'isin':i,'side':'BUY_REBAL','qty':q,'px':px,'fee':fee})
                active_rebalance['buys_remaining'].discard(i)
                if not active_rebalance['buys_remaining']:
                    signal_to_complete[str(sd.date())]=(d-sd).days; active_rebalance=None

        if d in targets:
            future_dates=dates[dates>d]
            if len(future_dates)==0:continue
            if active_rebalance is not None:
                raise SystemExit(f'BLOCK_OVERLAPPING_REBALANCE {active_rebalance["signal_date"].date()} -> {d.date()}')
            t=targets[d][:TOP_N]
            if len(t)!=TOP_N:raise SystemExit(f'BLOCK_TARGET_COUNT {d.date()} n={len(t)}')
            old=set(hold)
            active_rebalance={'signal_date':d,'targets':t,'sells_remaining':set(old),'buys_remaining':set(t),'buys_scheduled':False}
            if old:
                for i in old:
                    sd=first_quote_after(byisin,i,d)
                    if sd is None or (sd-d).days>MAX_LEG_DELAY_CAL_DAYS:
                        raise SystemExit(f'BLOCK_SELL_DELAY {d.date()} {i} sell={sd}')
                    enqueue(sd,{'type':'SELL','isin':i,'signal_date':d})
            else:
                ready=d; tv=cash/TOP_N; active_rebalance['buys_scheduled']=True
                for j in t:
                    bd=first_quote_after(byisin,j,d)
                    if bd is None or (bd-d).days>MAX_LEG_DELAY_CAL_DAYS:
                        raise SystemExit(f'BLOCK_BUY_DELAY {d.date()} {j} ready={d.date()} buy={bd}')
                    enqueue(bd,{'type':'BUY','isin':j,'signal_date':d,'target_value':tv})

        if len(hold)>TOP_N:raise SystemExit('BLOCK_MAX_HOLDINGS')
        eqrows.append({'date':d,'equity_eur':mark(),'cash_eur':cash,'open_positions':len(hold)})

    last=dates[-1]
    for i,q in list(hold.items()):
        raw=lastpx[i]; px=raw*(1-slip); fee=q*px*FEE; cash+=q*px-fee
        trades.append({'date':last,'signal_date':last,'isin':i,'side':'SELL_FINAL','qty':q,'px':px,'fee':fee})
    hold.clear()
    eq=pd.DataFrame(eqrows); eq.loc[eq.index[-1],['equity_eur','cash_eur','open_positions']]=[cash,cash,0]
    tr=pd.DataFrame(trades,columns=['date','signal_date','isin','side','qty','px','fee'])
    return eq,tr,metrics(eq,tr,'stress' if stress else 'base',signal_to_complete)


def sub(eq):
    out=[]
    for name,a,b in [('2010_2016','2010-01-01','2017-01-01'),('2017_2022','2017-01-01','2023-01-01')]:
        g=eq[(eq.date>=a)&(eq.date<b)].copy()
        if len(g)<2:out.append({'period':name,'status':'insufficient'});continue
        f=float(g.equity_eur.iloc[0]);l=float(g.equity_eur.iloc[-1]);yrs=max((g.date.iloc[-1]-g.date.iloc[0]).days/365.2425,1e-9);dd=g.equity_eur/g.equity_eur.cummax()-1
        out.append({'period':name,'start_equity':f,'end_equity':l,'return':l/f-1,'cagr':(l/f)**(1/yrs)-1,'max_drawdown':float(dd.min())})
    return out


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--history',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);a=ap.parse_args()
    z=norm(pd.read_parquet(a.history));s=build_signals(z)
    if s.empty:raise SystemExit('BLOCK_NO_SIGNALS')
    be,bt,bm=simulate(z,s,False);se,st,sm=simulate(z,s,True)
    a.out_dir.mkdir(parents=True,exist_ok=True)
    s.to_csv(a.out_dir/'MOMENTUM_V4_SIGNALS_PRE2023.csv',index=False);be.to_csv(a.out_dir/'MOMENTUM_V4_EQUITY_BASE_PRE2023.csv',index=False);bt.to_csv(a.out_dir/'MOMENTUM_V4_TRADES_BASE_PRE2023.csv',index=False)
    r={'version':'TABPORT_V23_XSEC_MOMENTUM_4_CAUSAL_MULTI_EXCHANGE_2','hypothesis':'unchanged classic 12-1 momentum, top-10 monthly equal-weight target',
       'execution_correction':'two-stage causal multi-exchange execution: old holdings sell on each own first quote after signal; after all sells complete, target notional is frozen and buys execute on each target own first quote strictly after readiness; each leg capped at 10 calendar days',
       'governance':{'holdout_2023_2026_accessed':False,'variant_count':1,'tuning':False,'signal_rule_changed':False,'survivorship_bias':True},
       'base':bm,'stress':sm,'base_subperiods':sub(be),'stress_subperiods':sub(se),
       'warnings':['Historical universe has survivorship bias','Price-only reconstruction','Five confirmed 2019 continuity holes are expected to be repaired by the supplied V23 overlay','Execution correction fixes multi-exchange calendar mechanics only; it does not alter ranking parameters']}
    (a.out_dir/'MOMENTUM_V4_REPORT_PRE2023.json').write_text(json.dumps(r,indent=2,default=str),encoding='utf-8');print(json.dumps(r,indent=2,default=str))

if __name__=='__main__':main()
