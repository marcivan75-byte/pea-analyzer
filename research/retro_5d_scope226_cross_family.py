from __future__ import annotations

import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from v182.hebdo.meta_price_history import load_2010_2026

ROUND_RATIOS=np.array([1.25,1.3333333333,1.5,1.6666666667,2.0,2.5,3.0,4.0,5.0,10.0])

def round_suspect(s):
    x=pd.to_numeric(s,errors='coerce').to_numpy(float); out=np.zeros(len(x),dtype=bool); ok=np.isfinite(x)
    if ok.any(): out[ok]=(np.abs(x[ok,None]-ROUND_RATIOS[None,:])/ROUND_RATIOS[None,:]).min(axis=1)<=0.005
    return out

def features(df):
    x=df.sort_values(['ticker','date']).reset_index(drop=True).copy(); x['date']=pd.to_datetime(x.date,utc=True).dt.tz_localize(None); g=x.groupby('ticker',group_keys=False)
    c=pd.to_numeric(x.close,errors='coerce'); h=pd.to_numeric(x.high,errors='coerce'); l=pd.to_numeric(x.low,errors='coerce'); v=pd.to_numeric(x.volume,errors='coerce'); prev=g.close.shift(1)
    x['date_t5']=g.date.shift(-5); x['close_t5']=g.close.shift(-5); x['ret_fwd5_pct']=(x.close_t5/c-1)*100; x['future_ratio']=x.close_t5/c
    dr=c/prev-1
    x['vol60']=dr.groupby(x.ticker).transform(lambda s:s.rolling(60,min_periods=60).std(ddof=0))*math.sqrt(252)*100
    peak=g.close.transform(lambda s:s.rolling(252,min_periods=60).max()); dd=(c/peak-1)*100; x['dd1y']=dd.groupby(x.ticker).transform(lambda s:s.rolling(252,min_periods=60).min())
    d=c-prev; gain=d.clip(lower=0); loss=-d.clip(upper=0); ag=gain.groupby(x.ticker).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()); al=loss.groupby(x.ticker).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()); rsi=100-100/(1+ag/al.replace(0,np.nan)); x['rsi_d3']=rsi-rsi.groupby(x.ticker).shift(3)
    ema12=g.close.transform(lambda s:s.ewm(span=12,adjust=False,min_periods=12).mean()); ema26=g.close.transform(lambda s:s.ewm(span=26,adjust=False,min_periods=26).mean()); macd=ema12-ema26; sig=macd.groupby(x.ticker).transform(lambda s:s.ewm(span=9,adjust=False,min_periods=9).mean()); hist=macd-sig; x['macd_d1']=hist-hist.groupby(x.ticker).shift(1)
    p1m=(c/g.close.shift(21)-1)*100; x['mom1m_d1']=p1m-p1m.groupby(x.ticker).shift(1)
    mm200=g.close.transform(lambda s:s.rolling(200,min_periods=200).mean()); x['mm200_slope20_pct']=(mm200/mm200.groupby(x.ticker).shift(20)-1)*100; x['above_mm200']=(c>mm200).astype(float)
    vm20=g.volume.transform(lambda s:s.rolling(20,min_periods=20).mean()); rv=v/vm20.replace(0,np.nan); x['rvol20']=rv; x['rvol_d10']=rv-rv.groupby(x.ticker).shift(10)
    return x

def admissible(x):
    return np.isfinite(x.close_t5)&(x.close_t5>0)&(x.close>=1)&(x.volume>=5000)&(x.ret_fwd5_pct<=50)&(~round_suspect(x.future_ratio))

def metric(frame,mask,label,period):
    d=frame.loc[period].copy(); m=pd.Series(mask,index=frame.index).loc[d.index].fillna(False).astype(bool); y=(d.ret_fwd5_pct>20)
    n=int(m.sum()); wins=int((m&y).sum()); base=float(y.mean()) if len(d) else np.nan; rate=wins/n if n else np.nan; lift=rate/base if n and base>0 else np.nan
    return {'pattern':label,'period':period.name,'universe_n':int(len(d)),'signals':n,'wins_gt20_5d':wins,'precision_pct':100*rate if np.isfinite(rate) else np.nan,'base_rate_pct':100*base if np.isfinite(base) else np.nan,'lift':lift}

def main():
    raw=load_2010_2026('inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet','inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json','data/cache/actions')[['date','ticker','open','high','low','close','volume']]
    x=features(raw); x=x.loc[admissible(x)].copy(); x['year']=x.date.dt.year
    locked=pd.read_csv('inputs/retro5d/MATCHED_WINNERS_CONTROLS_DEV.csv'); locked['date']=pd.to_datetime(locked.date); winners=locked.loc[locked.winner_5d.astype(str).str.lower().eq('true') & (locked.date.dt.year<=2018),['ticker','date']].drop_duplicates()
    w=winners.merge(x,on=['ticker','date'],how='inner')
    specs={
      'DD1Y_LOW':('dd1y','LOW'),'VOL60_HIGH':('vol60','HIGH'),'RSI_D3_LOW':('rsi_d3','LOW'),'MACD_D1_LOW':('macd_d1','LOW'),
      'MOM1M_D1_LOW':('mom1m_d1','LOW'),'MM200_SLOPE20_LOW':('mm200_slope20_pct','LOW'),'RVOL20_HIGH':('rvol20','HIGH'),'RVOL_D10_HIGH':('rvol_d10','HIGH')
    }
    thresholds={k:float(pd.to_numeric(w[col],errors='coerce').median()) for k,(col,_) in specs.items()}
    cond={}
    for k,(col,direction) in specs.items():
        cond[k]=(x[col]<=thresholds[k]) if direction=='LOW' else (x[col]>=thresholds[k])
    cond['BELOW_MM200']=x.above_mm200<=0
    patterns={
      'P01_DD_RSI':['DD1Y_LOW','RSI_D3_LOW'],
      'P02_DD_VOL60_RSI':['DD1Y_LOW','VOL60_HIGH','RSI_D3_LOW'],
      'P03_DD_TREND_RVOL':['DD1Y_LOW','MM200_SLOPE20_LOW','RVOL20_HIGH'],
      'P04_VOL_RVOL_RSI':['VOL60_HIGH','RVOL_D10_HIGH','RSI_D3_LOW'],
      'P05_TREND_RSI_RVOL':['MM200_SLOPE20_LOW','RSI_D3_LOW','RVOL_D10_HIGH'],
      'P06_DD_MACD_RVOL':['DD1Y_LOW','MACD_D1_LOW','RVOL_D10_HIGH'],
      'P07_DD_MOM_TREND':['DD1Y_LOW','MOM1M_D1_LOW','MM200_SLOPE20_LOW'],
      'P08_DD_BELOW200_RVOL':['DD1Y_LOW','BELOW_MM200','RVOL20_HIGH'],
      'P09_VOL_TREND_RSI_RVOL':['VOL60_HIGH','MM200_SLOPE20_LOW','RSI_D3_LOW','RVOL_D10_HIGH'],
      'P10_DD_VOL_MACD_RVOL':['DD1Y_LOW','VOL60_HIGH','MACD_D1_LOW','RVOL_D10_HIGH']
    }
    periods={
      'DISCOVERY_2010_2018':(x.year<=2018),
      'VALIDATION_2019_2022':((x.year>=2019)&(x.year<=2022)),
      'OOS_2023_2026':(x.year>=2023)
    }
    rows=[]
    for name,parts in patterns.items():
        mask=np.ones(len(x),dtype=bool)
        for p in parts: mask &= np.asarray(cond[p].fillna(False),dtype=bool)
        for pname,pmask in periods.items():
            pmask=pmask.copy(); pmask.name=pname; rows.append(metric(x,mask,name,pmask))
    out=pd.DataFrame(rows)
    pivot=out.pivot(index='pattern',columns='period',values=['signals','precision_pct','base_rate_pct','lift'])
    val=out[out.period=='VALIDATION_2019_2022'].set_index('pattern'); oos=out[out.period=='OOS_2023_2026'].set_index('pattern')
    verdict=[]
    for name in patterns:
        ok=(val.loc[name,'signals']>=500 and oos.loc[name,'signals']>=500 and val.loc[name,'lift']>=1.5 and oos.loc[name,'lift']>=1.5)
        verdict.append({'pattern':name,'conditions':' + '.join(patterns[name]),'validation_signals':int(val.loc[name,'signals']),'validation_precision_pct':val.loc[name,'precision_pct'],'validation_lift':val.loc[name,'lift'],'oos_signals':int(oos.loc[name,'signals']),'oos_precision_pct':oos.loc[name,'precision_pct'],'oos_lift':oos.loc[name,'lift'],'status':'CROSS_FAMILY_CONFIRMED_OOS' if ok else 'NOT_CONFIRMED'})
    verdict=pd.DataFrame(verdict).sort_values(['status','oos_lift'],ascending=[True,False])
    od=Path('outputs/retro_5d_scope226_cross_family'); od.mkdir(parents=True,exist_ok=True)
    out.to_csv(od/'CROSS_FAMILY_PERIOD_METRICS.csv',index=False); verdict.to_csv(od/'CROSS_FAMILY_VERDICTS.csv',index=False)
    (od/'THRESHOLDS_DISCOVERY_ONLY.json').write_text(json.dumps({'thresholds':thresholds,'winner_anchor_n':len(w),'threshold_source':'winner anchors 2010-2018 only; OOS untouched'},indent=2),encoding='utf-8')
    summary={'patterns_tested':len(patterns),'confirmed_oos':int((verdict.status=='CROSS_FAMILY_CONFIRMED_OOS').sum()),'thresholds':thresholds,'top':verdict.head(10).to_dict(orient='records'),'holdout_used_for_tuning':False,'pit_strict_certified':False}
    (od/'SUMMARY.json').write_text(json.dumps(summary,indent=2,default=float),encoding='utf-8'); print(json.dumps(summary,indent=2,default=float))
if __name__=='__main__': main()
