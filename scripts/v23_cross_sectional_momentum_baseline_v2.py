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


def norm(df):
    m={str(c).strip().lower():c for c in df.columns}
    def pick(*xs):
        for x in xs:
            if x in m: return m[x]
        raise SystemExit(f'BLOCK_SCHEMA missing {xs}')
    z=pd.DataFrame({
        'isin':df[pick('isin')].astype(str),
        'date':pd.to_datetime(df[pick('date','datetime')],errors='coerce').dt.tz_localize(None).dt.normalize(),
        'close':pd.to_numeric(df[pick('close','adj_close','adjusted_close')],errors='coerce')})
    z=z.dropna().query('close>0').copy()
    z=z[z.date<CUTOFF].drop_duplicates(['isin','date'],keep='last').sort_values(['isin','date'])
    if z.empty or z.date.max()>=CUTOFF: raise SystemExit('BLOCK_GOVERNANCE')
    return z


def build_signals(z):
    parts=[]
    for isin,g in z.groupby('isin',sort=False):
        g=g.sort_values('date').copy()
        g['mom']=g.close.shift(SKIP)/g.close.shift(LOOKBACK)-1.0
        g['obs']=np.arange(len(g))+1
        g['month']=g.date.dt.to_period('M')
        parts.append(g[['isin','date','close','mom','obs','month']])
    x=pd.concat(parts,ignore_index=True)
    # One common portfolio decision date per calendar month: latest date observed anywhere in the universe.
    anchors=x.groupby('month',as_index=False).date.max().rename(columns={'date':'signal_date'})
    # For each stock, use its own latest observation in that month, which is necessarily known by the common anchor.
    last=x.sort_values(['isin','month','date']).groupby(['isin','month'],as_index=False).tail(1)
    s=last.merge(anchors,on='month',how='left')
    s=s[(s.obs>=MIN_OBS)&s.mom.notna()&(s.date<=s.signal_date)].copy()
    s['rank']=s.groupby('signal_date').mom.rank(method='first',ascending=False)
    s=s[s['rank']<=TOP_N].sort_values(['signal_date','rank','isin'])
    return s[['signal_date','date','isin','close','mom','rank']].rename(columns={'date':'feature_date'})


def metrics(eq,tr,mode):
    peak=eq.equity_eur.cummax(); dd=eq.equity_eur/peak-1
    daily=eq.set_index('date').equity_eur.pct_change().dropna()
    years=max((eq.date.iloc[-1]-eq.date.iloc[0]).days/365.2425,1e-9)
    final=float(eq.equity_eur.iloc[-1])
    return {'mode':mode,'start':str(eq.date.iloc[0].date()),'end':str(eq.date.iloc[-1].date()),
            'initial_capital_eur':INITIAL_CAPITAL,'final_equity_eur':final,'net_eur':final-INITIAL_CAPITAL,
            'net_return':final/INITIAL_CAPITAL-1,'cagr':(final/INITIAL_CAPITAL)**(1/years)-1,
            'max_drawdown':float(dd.min()),'annualized_volatility':float(daily.std(ddof=1)*np.sqrt(252)),
            'fees_eur':float(tr.fee.sum()) if len(tr) else 0.0,'trade_actions':int(len(tr)),
            'avg_open_positions':float(eq.open_positions.mean()),'top_n':TOP_N,'lookback_days':LOOKBACK,
            'skip_days':SKIP,'variant_count':1}


def simulate(z,signals,stress=False):
    slip=STRESS_SLIP if stress else 0.0
    bydate={pd.Timestamp(d):g.set_index('isin').close.to_dict() for d,g in z.groupby('date')}
    targets={pd.Timestamp(d):g.sort_values('rank').isin.tolist() for d,g in signals.groupby('signal_date')}
    dates=pd.DatetimeIndex(sorted(bydate))
    cash=INITIAL_CAPITAL; hold={}; lastpx={}; trades=[]; eqrows=[]
    pending=None; done=set(); target_value=None; pending_from=None

    def mark():
        return cash+sum(q*lastpx[i] for i,q in hold.items() if i in lastpx)

    for d in dates:
        d=pd.Timestamp(d); pxs=bydate[d]; lastpx.update({i:float(p) for i,p in pxs.items()})

        if pending is not None and d>pending_from:
            pset=set(pending)
            # First, fully exit names no longer selected when their first post-anchor price appears.
            for i in list(hold):
                if i in pset or i not in pxs: continue
                q=hold.pop(i); px=float(pxs[i])*(1-slip); fee=q*px*FEE; cash+=q*px-fee
                trades.append({'date':d,'isin':i,'side':'SELL','qty':q,'px':px,'fee':fee})
            # Each selected name is rebalanced exactly once, at its first available price after the anchor.
            for i in pending:
                if i in done or i not in pxs: continue
                raw=float(pxs[i]); cur=int(hold.get(i,0))
                buy_px=raw*(1+slip); sell_px=raw*(1-slip)
                desired=int(np.floor(target_value/(buy_px*(1+FEE))))
                if cur>desired:
                    q=cur-desired; fee=q*sell_px*FEE; cash+=q*sell_px-fee
                    if desired: hold[i]=desired
                    else: hold.pop(i,None)
                    trades.append({'date':d,'isin':i,'side':'SELL_REBAL','qty':q,'px':sell_px,'fee':fee})
                elif cur<desired:
                    q=desired-cur; fee=q*buy_px*FEE; cost=q*buy_px+fee
                    if cost>cash:
                        q=int(np.floor(cash/(buy_px*(1+FEE))))
                        fee=q*buy_px*FEE; cost=q*buy_px+fee
                    if q>0:
                        cash-=cost; hold[i]=cur+q
                        trades.append({'date':d,'isin':i,'side':'BUY_REBAL','qty':q,'px':buy_px,'fee':fee})
                done.add(i)
            if len(done)==len(pending) and all(i in pset for i in hold):
                pending=None; done=set(); target_value=None; pending_from=None

        eqrows.append({'date':d,'equity_eur':mark(),'cash_eur':cash,'open_positions':len(hold)})

        if d in targets:
            # A new month-end decision supersedes any unfinished prior transition. This should be rare and is fail-closed.
            pending=targets[d][:TOP_N]; done=set(); pending_from=d; target_value=mark()/TOP_N

    last=dates[-1]
    for i in list(hold):
        raw=lastpx[i]; q=hold.pop(i); px=raw*(1-slip); fee=q*px*FEE; cash+=q*px-fee
        trades.append({'date':last,'isin':i,'side':'SELL_FINAL','qty':q,'px':px,'fee':fee})
    eq=pd.DataFrame(eqrows)
    eq.loc[eq.index[-1],['equity_eur','cash_eur','open_positions']]=[cash,cash,0]
    tr=pd.DataFrame(trades,columns=['date','isin','side','qty','px','fee'])
    return eq,tr,metrics(eq,tr,'stress' if stress else 'base')


def sub(eq):
    out=[]
    for name,a,b in [('2010_2016','2010-01-01','2017-01-01'),('2017_2022','2017-01-01','2023-01-01')]:
        g=eq[(eq.date>=a)&(eq.date<b)].copy()
        if len(g)<2: out.append({'period':name,'status':'insufficient'}); continue
        f=float(g.equity_eur.iloc[0]); l=float(g.equity_eur.iloc[-1]); yrs=max((g.date.iloc[-1]-g.date.iloc[0]).days/365.2425,1e-9)
        dd=g.equity_eur/g.equity_eur.cummax()-1
        out.append({'period':name,'start_equity':f,'end_equity':l,'return':l/f-1,'cagr':(l/f)**(1/yrs)-1,'max_drawdown':float(dd.min())})
    return out


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--history',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    z=norm(pd.read_parquet(a.history)); s=build_signals(z)
    if s.empty: raise SystemExit('BLOCK_NO_SIGNALS')
    be,bt,bm=simulate(z,s,False); se,st,sm=simulate(z,s,True)
    a.out_dir.mkdir(parents=True,exist_ok=True)
    s.to_csv(a.out_dir/'MOMENTUM_V2_SIGNALS_PRE2023.csv',index=False); be.to_csv(a.out_dir/'MOMENTUM_V2_EQUITY_BASE_PRE2023.csv',index=False); bt.to_csv(a.out_dir/'MOMENTUM_V2_TRADES_BASE_PRE2023.csv',index=False)
    r={'version':'TABPORT_V23_XSEC_MOMENTUM_2_CORRECTED','hypothesis':'unchanged classic 12-1 momentum, top-10 monthly equal-weight target',
       'corrections':['common cross-sectional month-end anchor','latest PIT feature per stock as-of anchor','full monthly rebalance of retained names','asynchronous first available post-anchor execution'],
       'governance':{'holdout_2023_2026_accessed':False,'variant_count':1,'tuning':False,'survivorship_bias':True},
       'base':bm,'stress':sm,'base_subperiods':sub(be),'stress_subperiods':sub(se),
       'warnings':['Historical universe has survivorship bias','Price-only reconstruction; adjusted-close absent','This is a bug-correction rerun, not a hyperparameter optimization']}
    (a.out_dir/'MOMENTUM_V2_REPORT_PRE2023.json').write_text(json.dumps(r,indent=2,default=str),encoding='utf-8'); print(json.dumps(r,indent=2,default=str))

if __name__=='__main__': main()
