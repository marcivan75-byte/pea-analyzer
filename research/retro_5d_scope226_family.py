from __future__ import annotations

import argparse, json, math
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu
from sklearn.metrics import roc_auc_score
from v182.hebdo.meta_price_history import load_2010_2026

LAGS=[1,3,5,10,20]
FAMILIES={
 'TREND':['mm20','mm50','mm100','mm200','above_mm20','above_mm50','above_mm200'],
 'MOMENTUM_OSC':['rsi14','macd','macd_signal','macd_hist','perf_1m_pct','perf_3m_pct','perf_6m_pct','perf_1y_pct'],
 'VOL_RISK':['atr14','bb_upper','bb_mid','bb_lower','volatility_20d','volatility_60d','max_drawdown_1y'],
 'VOLUME':['volume','rvol20'],
 'REVERSAL_PRICE':['last_close','positive_reversal_flag','n_sessions']
}
BINARY={'above_mm20','above_mm50','above_mm200','positive_reversal_flag'}
ALIASES={'ohlcv_n_sessions':'n_sessions','ohlcv_last':'last_close','ohlcv_perf_1y':'perf_1y_pct'}

def add_family_features(df,family):
    x=df.sort_values(['ticker','date']).reset_index(drop=True).copy()
    x['date']=pd.to_datetime(x['date'],utc=True).dt.tz_localize(None)
    g=x.groupby('ticker',group_keys=False)
    c=pd.to_numeric(x.close,errors='coerce'); h=pd.to_numeric(x.high,errors='coerce'); l=pd.to_numeric(x.low,errors='coerce'); o=pd.to_numeric(x.open,errors='coerce'); v=pd.to_numeric(x.volume,errors='coerce')
    prev=g.close.shift(1)
    x['n_sessions']=g.cumcount()+1; x['last_close']=c
    if family=='TREND':
        for n in [20,50,100,200]: x[f'mm{n}']=g.close.transform(lambda s,n=n:s.rolling(n,min_periods=n).mean())
        for n in [20,50,200]: x[f'above_mm{n}']=(c>x[f'mm{n}']).astype(float)
    elif family=='MOMENTUM_OSC':
        for n,name in [(21,'perf_1m_pct'),(63,'perf_3m_pct'),(126,'perf_6m_pct'),(252,'perf_1y_pct')]: x[name]=(c/g.close.shift(n)-1)*100
        ema12=g.close.transform(lambda s:s.ewm(span=12,adjust=False,min_periods=12).mean()); ema26=g.close.transform(lambda s:s.ewm(span=26,adjust=False,min_periods=26).mean())
        x['macd']=ema12-ema26; x['macd_signal']=x.groupby('ticker').macd.transform(lambda s:s.ewm(span=9,adjust=False,min_periods=9).mean()); x['macd_hist']=x.macd-x.macd_signal
        d=c-prev; gain=d.clip(lower=0); loss=-d.clip(upper=0)
        ag=gain.groupby(x.ticker).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()); al=loss.groupby(x.ticker).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean())
        x['rsi14']=100-100/(1+ag/al.replace(0,np.nan))
    elif family=='VOL_RISK':
        mm20=g.close.transform(lambda s:s.rolling(20,min_periods=20).mean()); sd20=g.close.transform(lambda s:s.rolling(20,min_periods=20).std(ddof=0))
        x['bb_mid']=mm20; x['bb_upper']=mm20+2*sd20; x['bb_lower']=mm20-2*sd20
        tr=pd.concat([(h-l).abs(),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1); x['atr14']=tr.groupby(x.ticker).transform(lambda s:s.rolling(14,min_periods=14).mean())
        dr=c/prev-1
        x['volatility_20d']=dr.groupby(x.ticker).transform(lambda s:s.rolling(20,min_periods=20).std(ddof=0))*math.sqrt(252)*100
        x['volatility_60d']=dr.groupby(x.ticker).transform(lambda s:s.rolling(60,min_periods=60).std(ddof=0))*math.sqrt(252)*100
        peak=g.close.transform(lambda s:s.rolling(252,min_periods=60).max()); dd=(c/peak-1)*100; x['max_drawdown_1y']=dd.groupby(x.ticker).transform(lambda s:s.rolling(252,min_periods=60).min())
    elif family=='VOLUME':
        x['volume']=v; vm=g.volume.transform(lambda s:s.rolling(20,min_periods=20).mean()); x['rvol20']=v/vm.replace(0,np.nan)
    elif family=='REVERSAL_PRICE':
        dr=c/prev-1; x['positive_reversal_flag']=((c>o)&(dr.shift(1)<0)&(c>prev)).astype(float)
    else: raise ValueError(family)
    cols=['ticker','date']+FAMILIES[family]
    z=x[cols].copy()
    delta={}
    for f in FAMILIES[family]:
        if f in BINARY: continue
        for lag in LAGS: delta[f'{f}_d{lag}']=x[f]-x.groupby('ticker')[f].shift(lag)
    if delta: z=pd.concat([z,pd.DataFrame(delta,index=x.index)],axis=1)
    return z

def bh(p):
    p=np.asarray(p,float); n=len(p); order=np.argsort(p); r=p[order]; q=r*n/np.arange(1,n+1); q=np.minimum.accumulate(q[::-1])[::-1]; out=np.empty(n); out[order]=np.minimum(q,1); return out

def screen(m,family):
    d=m.loc[pd.to_datetime(m.date).dt.year<=2018].copy(); rows=[]
    vars=[]
    for f in FAMILIES[family]:
        vars.append((f,f,'LEVEL'))
        if f not in BINARY:
            vars += [(f,f'{f}_d{lag}',f'DELTA_{lag}') for lag in LAGS]
    for base,col,kind in vars:
        a=pd.to_numeric(d.loc[d.winner_5d.astype(bool),col],errors='coerce').replace([np.inf,-np.inf],np.nan).dropna(); b=pd.to_numeric(d.loc[~d.winner_5d.astype(bool),col],errors='coerce').replace([np.inf,-np.inf],np.nan).dropna()
        if min(len(a),len(b))<50: continue
        p=float(mannwhitneyu(a,b,alternative='two-sided').pvalue)
        y=np.r_[np.ones(len(a)),np.zeros(len(b))]; z=np.r_[a.to_numpy(),b.to_numpy()]
        try: auc=float(roc_auc_score(y,z))
        except: auc=np.nan
        sd=math.sqrt((a.var(ddof=1)+b.var(ddof=1))/2); smd=(a.mean()-b.mean())/sd if np.isfinite(sd) and sd>0 else np.nan
        rows.append({'family':family,'base_criterion':base,'variable':col,'kind':kind,'winner_n':len(a),'control_n':len(b),'winner_median':a.median(),'control_median':b.median(),'smd':smd,'auc_raw':auc,'auc_discrimination':max(auc,1-auc) if np.isfinite(auc) else np.nan,'direction':'HIGH' if a.median()>=b.median() else 'LOW','p_value':p})
    out=pd.DataFrame(rows); out['q_value_bh']=bh(out.p_value.to_numpy()); out['score']=(out.auc_discrimination-0.5).abs()*2+out.smd.abs().clip(0,3)/3
    return out.sort_values(['score','q_value_bh'],ascending=[False,True])

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--family',required=True,choices=FAMILIES); ap.add_argument('--matched',default='inputs/retro5d/MATCHED_WINNERS_CONTROLS_DEV.csv'); ap.add_argument('--out',default='outputs/retro_5d_scope226_families'); args=ap.parse_args()
    matched=pd.read_csv(args.matched); matched['date']=pd.to_datetime(matched.date); matched['winner_5d']=matched.winner_5d.astype(str).str.lower().map({'true':True,'false':False}).fillna(matched.winner_5d.astype(bool))
    raw=load_2010_2026('inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet','inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json','data/cache/actions')[['date','ticker','open','high','low','close','volume']]
    feat=add_family_features(raw,args.family)
    keys=matched[['case_id','role','date','ticker','winner_5d','ret_fwd5_pct']].copy(); joined=keys.merge(feat,on=['ticker','date'],how='left',validate='many_to_one')
    out=screen(joined,args.family); od=Path(args.out); od.mkdir(parents=True,exist_ok=True); out.to_csv(od/f'{args.family}_FACTOR_SCREEN.csv',index=False)
    summary={'family':args.family,'matched_rows':len(joined),'winner_rows':int(joined.winner_5d.sum()),'variables_tested':len(out),'fdr_005':int((out.q_value_bh<0.05).sum()),'best':out.head(10).to_dict(orient='records')}; (od/f'{args.family}_SUMMARY.json').write_text(json.dumps(summary,indent=2,default=float),encoding='utf-8'); print(json.dumps(summary,indent=2,default=float))
if __name__=='__main__': main()
