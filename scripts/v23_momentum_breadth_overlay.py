from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

INITIAL_CAPITAL = 65000.0
FEE = 0.002
STRESS_SLIP = 0.001
CUTOFF = pd.Timestamp('2023-01-01')
TOP_N = 10
LOOKBACK = 252
SKIP = 21
MIN_OBS = 253
BREADTH_MA = 200
BREADTH_THRESHOLD = 0.50


def norm(df: pd.DataFrame) -> pd.DataFrame:
    m = {str(c).strip().lower(): c for c in df.columns}
    def pick(*xs):
        for x in xs:
            if x in m:
                return m[x]
        raise SystemExit(f'BLOCK_SCHEMA missing {xs}')
    z = pd.DataFrame({
        'isin': df[pick('isin')].astype(str),
        'date': pd.to_datetime(df[pick('date','datetime')], errors='coerce').dt.tz_localize(None).dt.normalize(),
        'close': pd.to_numeric(df[pick('close','adj_close','adjusted_close')], errors='coerce'),
    }).dropna()
    z = z[(z['close'] > 0) & (z['date'] < CUTOFF)]
    z = z.drop_duplicates(['isin','date'], keep='last').sort_values(['isin','date'])
    if z.empty or z['date'].max() >= CUTOFF:
        raise SystemExit('BLOCK_GOVERNANCE')
    return z


def build_decisions(z: pd.DataFrame):
    parts = []
    for isin, g in z.groupby('isin', sort=False):
        g = g.sort_values('date').copy()
        g['mom'] = g['close'].shift(SKIP) / g['close'].shift(LOOKBACK) - 1.0
        g['ma200'] = g['close'].rolling(BREADTH_MA, min_periods=BREADTH_MA).mean()
        g['obs'] = np.arange(len(g)) + 1
        g['month'] = g['date'].dt.to_period('M')
        parts.append(g[['isin','date','close','mom','ma200','obs','month']])
    x = pd.concat(parts, ignore_index=True)
    anchors = x.groupby('month', as_index=False)['date'].max().rename(columns={'date':'signal_date'})
    last = x.sort_values(['isin','month','date']).groupby(['isin','month'], as_index=False).tail(1)
    s = last.merge(anchors, on='month', how='left')
    s = s[(s['date'] <= s['signal_date']) & (s['obs'] >= MIN_OBS) & s['mom'].notna() & s['ma200'].notna()].copy()
    if s.empty:
        raise SystemExit('BLOCK_NO_SIGNALS')

    breadth = (s.assign(above_ma=s['close'] > s['ma200'])
                 .groupby('signal_date', as_index=False)
                 .agg(eligible=('isin','size'), above_ma=('above_ma','sum')))
    breadth['breadth'] = breadth['above_ma'] / breadth['eligible']
    breadth['risk_on'] = breadth['breadth'] >= BREADTH_THRESHOLD

    s['rank'] = s.groupby('signal_date')['mom'].rank(method='first', ascending=False)
    tops = s[s['rank'] <= TOP_N].sort_values(['signal_date','rank','isin'])
    targets = {}
    for row in breadth.itertuples(index=False):
        d = pd.Timestamp(row.signal_date)
        if bool(row.risk_on):
            g = tops[tops['signal_date'] == d]
            targets[d] = g['isin'].tolist()[:TOP_N]
        else:
            targets[d] = []
    signal_table = tops[['signal_date','date','isin','close','mom','ma200','rank']].rename(columns={'date':'feature_date'})
    return signal_table, breadth.sort_values('signal_date'), targets


def subperiods(eq: pd.DataFrame):
    out=[]
    for name,a,b in [('2010_2016','2010-01-01','2017-01-01'),('2017_2022','2017-01-01','2023-01-01')]:
        g=eq[(eq['date']>=a)&(eq['date']<b)].copy()
        if len(g)<2:
            out.append({'period':name,'status':'insufficient'}); continue
        f=float(g['equity_eur'].iloc[0]); l=float(g['equity_eur'].iloc[-1])
        yrs=max((g['date'].iloc[-1]-g['date'].iloc[0]).days/365.2425,1e-9)
        dd=g['equity_eur']/g['equity_eur'].cummax()-1
        out.append({'period':name,'start_equity':f,'end_equity':l,'return':l/f-1,
                    'cagr':(l/f)**(1/yrs)-1,'max_drawdown':float(dd.min())})
    return out


def simulate(z: pd.DataFrame, targets: dict, stress: bool=False):
    slip = STRESS_SLIP if stress else 0.0
    bydate={pd.Timestamp(d):g.set_index('isin')['close'].to_dict() for d,g in z.groupby('date')}
    dates=pd.DatetimeIndex(sorted(bydate))
    cash=INITIAL_CAPITAL; hold={}; lastpx={}; trades=[]; eqrows=[]
    pending_buys={}; pending_sells=set(); pending_from=None
    cancelled_unfilled=0; stuck_sell_events=[]

    def mark():
        return cash + sum(q*lastpx[i] for i,q in hold.items() if i in lastpx)

    for d in dates:
        d=pd.Timestamp(d); pxs=bydate[d]
        lastpx.update({i:float(p) for i,p in pxs.items()})

        if pending_from is not None and d > pending_from:
            # Sells first. If no quote, order remains pending until next anchor.
            for i in list(pending_sells):
                if i not in pxs or i not in hold:
                    continue
                q=hold.pop(i); px=float(pxs[i])*(1-slip); fee=q*px*FEE
                cash += q*px-fee
                trades.append({'date':d,'isin':i,'side':'SELL','qty':q,'px':px,'fee':fee})
                pending_sells.remove(i)
            # Each buy has a fixed euro budget set at the anchor and can fill once only.
            for i in list(pending_buys):
                if i not in pxs:
                    continue
                budget=float(pending_buys.pop(i)); raw=float(pxs[i]); px=raw*(1+slip)
                desired=int(np.floor(budget/(px*(1+FEE))))
                cur=int(hold.get(i,0))
                q=max(desired-cur,0)
                if q>0:
                    fee=q*px*FEE; cost=q*px+fee
                    if cost>cash:
                        q=int(np.floor(cash/(px*(1+FEE)))); fee=q*px*FEE; cost=q*px+fee
                    if q>0:
                        cash-=cost; hold[i]=cur+q
                        trades.append({'date':d,'isin':i,'side':'BUY','qty':q,'px':px,'fee':fee})

        eqrows.append({'date':d,'equity_eur':mark(),'cash_eur':cash,'open_positions':len(hold)})

        if d in targets:
            # Cancel unfilled buys from the prior month; never replace using future knowledge.
            cancelled_unfilled += len(pending_buys)
            pending_buys={}
            # A sell still unfilled at the next decision is a data/corporate-action gap.
            if pending_sells:
                stuck_sell_events.append({'anchor':str(d.date()),'isins':sorted(pending_sells)})
            desired=targets[d][:TOP_N]
            desired_set=set(desired)
            pending_sells |= {i for i in hold if i not in desired_set}
            anchor_equity=mark()
            budget=(anchor_equity/TOP_N) if desired else 0.0
            for i in desired:
                if i not in hold:
                    pending_buys[i]=budget
                else:
                    # No retained-name rebalance: reduces turnover and avoids asynchronous sizing artefacts.
                    pass
            pending_from=d

    # Final liquidation where a final quote exists; fail-closed on positions with no terminal tradable quote.
    final_date=dates[-1]
    terminal_stuck=[]
    for i in list(hold):
        if i not in bydate[final_date]:
            terminal_stuck.append(i)
            continue
        q=hold.pop(i); raw=float(bydate[final_date][i]); px=raw*(1-slip); fee=q*px*FEE
        cash+=q*px-fee; trades.append({'date':final_date,'isin':i,'side':'SELL_FINAL','qty':q,'px':px,'fee':fee})
    if terminal_stuck:
        raise SystemExit('BLOCK_TERMINAL_STUCK_HOLDINGS '+','.join(sorted(terminal_stuck)))

    eq=pd.DataFrame(eqrows)
    eq.loc[eq.index[-1],['equity_eur','cash_eur','open_positions']]=[cash,cash,0]
    tr=pd.DataFrame(trades,columns=['date','isin','side','qty','px','fee'])
    peak=eq['equity_eur'].cummax(); dd=eq['equity_eur']/peak-1
    daily=eq.set_index('date')['equity_eur'].pct_change().dropna()
    yrs=max((eq['date'].iloc[-1]-eq['date'].iloc[0]).days/365.2425,1e-9)
    final=float(eq['equity_eur'].iloc[-1])
    met={'mode':'stress' if stress else 'base','start':str(eq['date'].iloc[0].date()),'end':str(eq['date'].iloc[-1].date()),
         'initial_capital_eur':INITIAL_CAPITAL,'final_equity_eur':final,'net_eur':final-INITIAL_CAPITAL,
         'net_return':final/INITIAL_CAPITAL-1,'cagr':(final/INITIAL_CAPITAL)**(1/yrs)-1,
         'max_drawdown':float(dd.min()),'annualized_volatility':float(daily.std(ddof=1)*np.sqrt(252)),
         'fees_eur':float(tr['fee'].sum()) if len(tr) else 0.0,'trade_actions':int(len(tr)),
         'avg_open_positions':float(eq['open_positions'].mean()),'cancelled_unfilled_buys':int(cancelled_unfilled),
         'stuck_sell_events':int(len(stuck_sell_events)),'top_n':TOP_N,'variant_count':1}
    return eq,tr,met,stuck_sell_events


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--history',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    z=norm(pd.read_parquet(a.history)); signals,breadth,targets=build_decisions(z)
    be,bt,bm,bstuck=simulate(z,targets,False); se,st,sm,sstuck=simulate(z,targets,True)
    a.out_dir.mkdir(parents=True,exist_ok=True)
    signals.to_csv(a.out_dir/'MOM_BREADTH_SIGNALS_PRE2023.csv',index=False)
    breadth.to_csv(a.out_dir/'MOM_BREADTH_REGIMES_PRE2023.csv',index=False)
    be.to_csv(a.out_dir/'MOM_BREADTH_EQUITY_BASE_PRE2023.csv',index=False)
    bt.to_csv(a.out_dir/'MOM_BREADTH_TRADES_BASE_PRE2023.csv',index=False)
    report={
      'version':'TABPORT_V23_MOMENTUM_BREADTH_1_FROZEN',
      'hypothesis':'classic 12-1 top-10 momentum; binary cash overlay when PIT breadth above 200d MA is below 50%',
      'parameters':{'lookback_obs':LOOKBACK,'skip_obs':SKIP,'top_n':TOP_N,'breadth_ma_obs':BREADTH_MA,'breadth_threshold':BREADTH_THRESHOLD},
      'governance':{'holdout_2023_2026_accessed':False,'variant_count':1,'tuning':False,'survivorship_bias':True,'future_fill_used_for_selection':False},
      'execution':['signals at common monthly anchor','fills strictly after anchor','unfilled buys cancelled at next anchor','no future tradability filter','retained names not resized'],
      'breadth':{'risk_on_months':int(breadth['risk_on'].sum()),'risk_off_months':int((~breadth['risk_on']).sum()),'total_months':int(len(breadth))},
      'base':bm,'stress':sm,'base_subperiods':subperiods(be),'stress_subperiods':subperiods(se),
      'base_stuck_sell_events':bstuck,'stress_stuck_sell_events':sstuck,
      'warnings':['Historical universe has survivorship bias','Price-only reconstruction; corporate-action/delisting handling is incomplete','Single frozen overlay; no threshold search']
    }
    (a.out_dir/'MOM_BREADTH_REPORT_PRE2023.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print(json.dumps(report,indent=2,default=str))

if __name__=='__main__': main()
