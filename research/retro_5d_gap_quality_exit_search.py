from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf
from v182.hebdo.meta_price_history import load_2010_2026

OUT=Path('outputs/retro_5d_gap_quality_exit_search'); OUT.mkdir(parents=True,exist_ok=True)
ROUND=np.array([1.25,4/3,1.5,5/3,2,2.5,3,4,5,10],float)

def split_suspect(s,tol=.005):
    a=pd.to_numeric(s,errors='coerce').to_numpy(float); out=np.zeros(len(a),bool); ok=np.isfinite(a)&(a>0); rr=np.r_[ROUND,1/ROUND]
    if ok.any(): out[ok]=(np.abs(a[ok,None]-rr[None,:])/rr[None,:]).min(axis=1)<=tol
    return pd.Series(out,index=s.index)

def load():
    x=load_2010_2026('inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet','inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json','data/cache/actions')[['date','ticker','open','high','low','close','volume']].copy()
    x.date=pd.to_datetime(x.date,utc=True).dt.tz_localize(None)
    for c in ['open','high','low','close','volume']: x[c]=pd.to_numeric(x[c],errors='coerce')
    return x.dropna().sort_values(['ticker','date']).drop_duplicates(['ticker','date'],keep='last').reset_index(drop=True)

def bench(a,b):
    z=yf.download('^STOXX50E',start=(a-pd.Timedelta(days=200)).strftime('%Y-%m-%d'),end=(b+pd.Timedelta(days=10)).strftime('%Y-%m-%d'),auto_adjust=False,repair=False,progress=False,threads=False)
    if isinstance(z.columns,pd.MultiIndex): z.columns=z.columns.get_level_values(0)
    q=pd.DataFrame({'date':pd.to_datetime(z.index).tz_localize(None),'bc':pd.to_numeric(z.Close,errors='coerce').to_numpy()}).dropna(); q['br90']=q.bc.pct_change(90,fill_method=None); return q

def prepare(x,b):
    g=x.groupby('ticker',sort=False,group_keys=False); prev=g.close.shift(1)
    x['gap_pct']=(x.open/prev-1)*100; x['split_suspect']=split_suspect(x.open/prev)
    rng=(x.high-x.low).replace(0,np.nan); x['close_loc']=(x.close-x.low)/rng
    x['ret90']=g.close.pct_change(90,fill_method=None)
    turnover=x.close*x.volume; x['adv20']=turnover.groupby(x.ticker).transform(lambda s:s.rolling(20,min_periods=20).mean())
    x=x.merge(b,on='date',how='left'); x['rs']=(1+x.ret90)/(1+x.br90)-1; x['rs_rank']=x.groupby('date').rs.rank(pct=True)*100
    x['year']=x.date.dt.year; x['period']=np.select([x.year<=2018,x.year<=2022],['DISC','VAL'],default='OOS')
    # per-ticker forward OHLC J+1..J+5
    gg=x.groupby('ticker',sort=False,group_keys=False)
    for k in range(1,6):
        x[f'date_{k}']=gg.date.shift(-k); x[f'open_{k}']=gg.open.shift(-k); x[f'high_{k}']=gg.high.shift(-k); x[f'low_{k}']=gg.low.shift(-k); x[f'close_{k}']=gg.close.shift(-k)
    return x

def candidate_mask(x):
    return ((x.close>=1)&(x.open>=1)&(x.volume>=5000)&(x.adv20>=800000)&(~x.split_suspect)&(x.gap_pct>=7.5)&(x.close_loc>=.8)&(x.rs_rank>=70)&x.open_1.notna()&x.close_5.notna())

def simulate(row, stop_pct=None, target_pct=None):
    entry=float(row.open_1)
    if not np.isfinite(entry) or entry<=0: return np.nan,'INVALID',np.nan
    stop=entry*(1-stop_pct/100) if stop_pct else None; target=entry*(1+target_pct/100) if target_pct else None
    mfe=-np.inf; mae=np.inf
    for k in range(1,6):
        hi=float(row[f'high_{k}']); lo=float(row[f'low_{k}'])
        mfe=max(mfe,(hi/entry-1)*100); mae=min(mae,(lo/entry-1)*100)
        hit_stop=stop is not None and lo<=stop
        hit_target=target is not None and hi>=target
        if hit_stop and hit_target: return -stop_pct,'STOP_FIRST_COLLISION',k
        if hit_stop: return -stop_pct,'STOP',k
        if hit_target: return target_pct,'TARGET',k
    ret=(float(row.close_5)/entry-1)*100
    return ret,'TIME',5

def metrics(trades,retcol,period):
    z=trades.loc[trades.period==period,retcol].dropna(); n=len(z); wins=z[z>0]; losses=z[z<0]
    pf=float(wins.sum()/(-losses.sum())) if len(losses) and losses.sum()<0 else np.inf
    return {'n':int(n),'win_rate_pct':100*float((z>0).mean()) if n else np.nan,'mean_pct':float(z.mean()) if n else np.nan,'median_pct':float(z.median()) if n else np.nan,'pf':pf,'worst_pct':float(z.min()) if n else np.nan,'best_pct':float(z.max()) if n else np.nan}

def main():
    raw=load(); x=prepare(raw,bench(raw.date.min(),raw.date.max())); trades=x.loc[candidate_mask(x)].copy().reset_index(drop=True)
    configs=[('TIME_ONLY',None,None)]
    for s in [3,5,7,10,12]:
        configs.append((f'STOP{s}',s,None))
        for t in [10,15,20,25]: configs.append((f'STOP{s}_TARGET{t}',s,t))
    for t in [10,15,20,25]: configs.append((f'TARGET{t}',None,t))
    rows=[]
    for name,s,t in configs:
        vals=[simulate(r,s,t) for _,r in trades.iterrows()]
        trades[name+'_ret']=[v[0] for v in vals]; trades[name+'_reason']=[v[1] for v in vals]; trades[name+'_day']=[v[2] for v in vals]
        rec={'config':name,'stop_pct':s,'target_pct':t}
        for per in ['DISC','VAL','OOS']:
            m=metrics(trades,name+'_ret',per)
            for k,v in m.items(): rec[f'{per}_{k}']=v
        # Rank only on pre-OOS robust expectancy then PF; OOS never enters ranking.
        rec['pre_oos_min_mean']=min(rec['DISC_mean_pct'],rec['VAL_mean_pct'])
        rec['pre_oos_min_pf']=min(rec['DISC_pf'],rec['VAL_pf'])
        rows.append(rec)
    out=pd.DataFrame(rows).sort_values(['pre_oos_min_mean','pre_oos_min_pf'],ascending=[False,False])
    out.to_csv(OUT/'EXIT_CONFIGS_DISC_VAL_OOS.csv',index=False)
    best=out.iloc[0]
    # publish compact trade detail for selected config
    bname=best['config']; cols=['date','ticker','period','gap_pct','close_loc','rs_rank','open_1','close_5',bname+'_ret',bname+'_reason',bname+'_day']
    trades[cols].to_csv(OUT/'BEST_CONFIG_TRADES.csv',index=False)
    summary={'candidate':'GAP>=7.5 + CLOSE_LOCATION>=0.8 + RS_RANK>=70','trades':int(len(trades)),'configs_tested':int(len(out)),'selection':'maximize min(DISC mean, VAL mean), tie-break min PF; OOS untouched','best_pre_oos':best.to_dict()}
    (OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2,default=float),encoding='utf-8'); print(json.dumps(summary,indent=2,default=float)); print(out.head(15).to_string(index=False))
if __name__=='__main__': main()
