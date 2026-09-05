from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf
from v182.hebdo.meta_price_history import load_2010_2026

OUT=Path('outputs/retro_5d_portfolio_validation'); OUT.mkdir(parents=True,exist_ok=True)
ROUND=np.array([1.25,4/3,1.5,5/3,2,2.5,3,4,5,10],float)
PATTERN='GAP>=7.5 + CLOSE_LOC>=0.8 + RS>=70'


def split_suspect(s,tol=.005):
    a=pd.to_numeric(s,errors='coerce').to_numpy(float); out=np.zeros(len(a),bool); ok=np.isfinite(a)&(a>0); rr=np.r_[ROUND,1/ROUND]
    if ok.any(): out[ok]=(np.abs(a[ok,None]-rr[None,:])/rr[None,:]).min(axis=1)<=tol
    return pd.Series(out,index=s.index)


def load_base():
    x=load_2010_2026('inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet','inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json','data/cache/actions')[['date','ticker','open','high','low','close','volume']].copy()
    x.date=pd.to_datetime(x.date,utc=True).dt.tz_localize(None)
    for c in ['open','high','low','close','volume']: x[c]=pd.to_numeric(x[c],errors='coerce')
    return x.dropna().sort_values(['ticker','date']).drop_duplicates(['ticker','date'],keep='last').reset_index(drop=True)


def load_bench(a,b):
    z=yf.download('^STOXX50E',start=(a-pd.Timedelta(days=200)).strftime('%Y-%m-%d'),end=(b+pd.Timedelta(days=10)).strftime('%Y-%m-%d'),auto_adjust=False,repair=False,progress=False,threads=False)
    if isinstance(z.columns,pd.MultiIndex): z.columns=z.columns.get_level_values(0)
    q=pd.DataFrame({'date':pd.to_datetime(z.index).tz_localize(None),'bc':pd.to_numeric(z.Close,errors='coerce').to_numpy()}).dropna()
    q['br90']=q.bc.pct_change(90,fill_method=None)
    return q[['date','br90']]


def features(x,b):
    g=x.groupby('ticker',sort=False,group_keys=False); prev=g.close.shift(1)
    x['gap_pct']=(x.open/prev-1)*100; x['split_suspect']=split_suspect(x.open/prev)
    rng=(x.high-x.low).replace(0,np.nan); x['close_loc']=(x.close-x.low)/rng
    x['ret90']=g.close.pct_change(90,fill_method=None)
    turnover=x.close*x.volume; x['adv20']=turnover.groupby(x.ticker).transform(lambda s:s.rolling(20,min_periods=20).mean())
    x=x.merge(b,on='date',how='left'); x['rs']=(1+x.ret90)/(1+x.br90)-1; x['rs_rank']=x.groupby('date').rs.rank(pct=True)*100
    x['signal']=(x.gap_pct>=7.5)&(x.close_loc>=.8)&(x.rs_rank>=70)&(~x.split_suspect)&(x.close>=1)&(x.open>=1)&(x.volume>=5000)&(x.adv20>=800000)
    return x


def build_trade_paths(x):
    rows=[]
    for t,z in x.groupby('ticker',sort=False):
        z=z.reset_index(drop=True); idx=np.flatnonzero(z.signal.to_numpy(bool))
        for i in idx:
            if i+5>=len(z): continue
            sig=z.iloc[i]; bars=z.iloc[i+1:i+6]
            if len(bars)<5 or not np.isfinite(bars.iloc[0].open) or bars.iloc[0].open<=0: continue
            row={'signal_date':sig.date,'ticker':t,'gap_pct':sig.gap_pct,'close_loc':sig.close_loc,'rs_rank':sig.rs_rank,'adv20':sig.adv20,'entry_date':bars.iloc[0].date,'entry_px':bars.iloc[0].open}
            for k,(_,b) in enumerate(bars.iterrows(),1): row[f'd{k}']=b.date; row[f'h{k}']=b.high; row[f'l{k}']=b.low; row[f'c{k}']=b.close
            rows.append(row)
    return pd.DataFrame(rows).sort_values(['entry_date','ticker']).reset_index(drop=True)


def exit_price(r,cfg):
    e=float(r.entry_px); stop=-.10 if cfg in ['SL10_TP20','DYN_BE_TP20'] else None; target=.20; armed=False
    for d in range(1,6):
        h=float(r[f'h{d}']); l=float(r[f'l{d}']); date=r[f'd{d}']; s=0.0 if (cfg=='DYN_BE_TP20' and armed) else stop
        hit_s=(s is not None and l<=e*(1+s)); hit_t=h>=e*(1+target)
        if hit_s and hit_t: return e*(1+s),date,'STOP_FIRST'
        if hit_s: return e*(1+s),date,'STOP'
        if hit_t: return e*(1+target),date,'TARGET20'
        if cfg=='DYN_BE_TP20' and h>=e*1.10: armed=True
    return float(r.c5),r.d5,'TIME5'


def trade_table(paths,cfg,bps):
    rows=[]; f=bps/10000
    for _,r in paths.iterrows():
        xp,xd,why=exit_price(r,cfg); ep=float(r.entry_px)*(1+f); xp_net=float(xp)*(1-f); ret=xp_net/ep-1
        rows.append({**r.to_dict(),'config':cfg,'friction_bps_oneway':bps,'exit_date':pd.Timestamp(xd),'exit_px_gross':xp,'exit_px_net':xp_net,'exit_reason':why,'net_ret':ret})
    return pd.DataFrame(rows)


def portfolio(trades,max_pos=5,initial=100000.0):
    t=trades.sort_values(['entry_date','ticker']).copy(); cash=initial; openpos=[]; accepted=[]; skipped=0
    dates=sorted(set(pd.to_datetime(t.entry_date))|set(pd.to_datetime(t.exit_date)))
    entries={pd.Timestamp(d):z for d,z in t.groupby('entry_date')}
    for d in dates:
        d=pd.Timestamp(d)
        # Entries occur at the open. Capital from positions exiting later the same day is NOT reusable.
        if d in entries:
            for _,r in entries[d].iterrows():
                if any(p['ticker']==r.ticker for p in openpos) or len(openpos)>=max_pos: skipped+=1; continue
                equity_est=cash+sum(p['notional'] for p in openpos); target_notional=equity_est/max_pos; notional=min(cash,target_notional)
                if notional<=0: skipped+=1; continue
                cash-=notional
                rec=r.to_dict(); rec['notional']=notional; rec['capacity_pct_adv20']=100*notional/max(float(r.adv20),1); openpos.append(rec); accepted.append(rec)
        # Then process all intraday/close exits, including positions entered and exited on this same date.
        exiting=[p for p in openpos if pd.Timestamp(p['exit_date'])==d]
        for p in exiting:
            cash += p['notional']*(1+p['net_ret']); openpos.remove(p)
    final=cash+sum(p['notional']*(1+p['net_ret']) for p in openpos)
    a=pd.DataFrame(accepted)
    if len(a): a['pnl_eur']=a.notional*a.net_ret
    return a, final, skipped


def annual_portfolio(tt,cfg,bps,maxp,years=range(2023,2027),initial=100000.0):
    rows=[]; detail=[]
    for y in years:
        q=tt[pd.to_datetime(tt.entry_date).dt.year==y].copy(); a,final,skipped=portfolio(q,maxp,initial)
        rows.append({'year':y,'config':cfg,'bps_oneway':bps,'max_positions':maxp,'signals':len(q),'accepted':len(a),'skipped_capacity':skipped,'final_equity_eur':final,'portfolio_return_pct':100*(final/initial-1),'wins':int((a.net_ret>0).sum()) if len(a) else 0,'win_rate_pct':100*(a.net_ret>0).mean() if len(a) else np.nan,'mean_trade_pct':100*a.net_ret.mean() if len(a) else np.nan,'median_trade_pct':100*a.net_ret.median() if len(a) else np.nan,'profit_factor':float(a.loc[a.pnl_eur>0,'pnl_eur'].sum()/(-a.loc[a.pnl_eur<0,'pnl_eur'].sum())) if len(a) and (a.pnl_eur<0).any() else np.inf})
        if len(a): detail.append(a.assign(portfolio_year=y,config=cfg))
    return pd.DataFrame(rows), (pd.concat(detail,ignore_index=True) if detail else pd.DataFrame())


def main():
    raw=load_base(); x=features(raw,load_bench(raw.date.min(),raw.date.max())); paths=build_trade_paths(x)
    cfgs=['TP20','SL10_TP20','DYN_BE_TP20']; bps_grid=[0,10,25,50]; max_grid=[3,5,10]
    summaries=[]; annuals=[]; details=[]
    for cfg in cfgs:
        for bps in bps_grid:
            tt=trade_table(paths,cfg,bps)
            for maxp in max_grid:
                for label,lo,hi in [('PRE',2010,2022),('OOS',2023,2026)]:
                    q=tt[(tt.entry_date.dt.year>=lo)&(tt.entry_date.dt.year<=hi)].copy(); a,final,skipped=portfolio(q,maxp)
                    summaries.append({'config':cfg,'bps_oneway':bps,'max_positions':maxp,'period':label,'signals':len(q),'accepted':len(a),'skipped_capacity':skipped,'final_equity_eur':final,'portfolio_return_pct':100*(final/100000-1),'mean_trade_pct':100*a.net_ret.mean() if len(a) else np.nan,'win_rate_pct':100*(a.net_ret>0).mean() if len(a) else np.nan,'median_capacity_pct_adv20':a.capacity_pct_adv20.median() if len(a) else np.nan,'max_capacity_pct_adv20':a.capacity_pct_adv20.max() if len(a) else np.nan})
                if bps==25 and maxp==5:
                    ann,det=annual_portfolio(tt,cfg,bps,maxp); annuals.append(ann); details.append(det)
    s=pd.DataFrame(summaries); s.to_csv(OUT/'PORTFOLIO_GRID.csv',index=False)
    pd.concat(annuals,ignore_index=True).to_csv(OUT/'OOS_ANNUAL_DEFAULT_25BPS_MAX5.csv',index=False)
    pd.concat(details,ignore_index=True).to_csv(OUT/'OOS_ACCEPTED_TRADES_DEFAULT.csv',index=False)
    focus=s[(s.period=='OOS')&(s.bps_oneway==25)&(s.max_positions==5)].sort_values('portfolio_return_pct',ascending=False); focus.to_csv(OUT/'OOS_DEFAULT_CONFIG_COMPARISON.csv',index=False)
    meta={'pattern':PATTERN,'entry':'Open J+1 after signal known at Close J','portfolio_initial_eur':100000,'default_max_positions':5,'default_all_in_friction_bps_oneway':25,'friction_note':'all-in slippage+fees proxy; excludes security-specific taxes such as French FTT','capacity':'position notional / trailing ADV20 EUR','event_order':'entries at open first; exits later same day; same-day released capital cannot finance same-day entries','dynamic_exit':'SL10; after any session reaches +10%, stop moves to breakeven from next session; TP20; time exit day5; same-day stop/target collision=STOP_FIRST','oos_not_used_for_pattern_or_exit_selection':True,'signals_total':len(paths),'focus':focus.to_dict('records')}
    (OUT/'SUMMARY.json').write_text(json.dumps(meta,indent=2,default=float),encoding='utf-8'); print(json.dumps(meta,indent=2,default=float))

if __name__=='__main__': main()
