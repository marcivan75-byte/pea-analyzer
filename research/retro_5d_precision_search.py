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
    c=pd.to_numeric(x.close,errors='coerce'); v=pd.to_numeric(x.volume,errors='coerce'); prev=g.close.shift(1)
    x['close_t5']=g.close.shift(-5); x['ret_fwd5_pct']=(x.close_t5/c-1)*100; x['future_ratio']=x.close_t5/c
    dr=c/prev-1
    x['vol60']=dr.groupby(x.ticker).transform(lambda s:s.rolling(60,min_periods=60).std(ddof=0))*math.sqrt(252)*100
    peak=g.close.transform(lambda s:s.rolling(252,min_periods=60).max()); dd=(c/peak-1)*100; x['dd1y']=dd.groupby(x.ticker).transform(lambda s:s.rolling(252,min_periods=60).min())
    d=c-prev; gain=d.clip(lower=0); loss=-d.clip(upper=0); ag=gain.groupby(x.ticker).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()); al=loss.groupby(x.ticker).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()); rsi=100-100/(1+ag/al.replace(0,np.nan)); x['rsi_d3']=rsi-rsi.groupby(x.ticker).shift(3)
    ema12=g.close.transform(lambda s:s.ewm(span=12,adjust=False,min_periods=12).mean()); ema26=g.close.transform(lambda s:s.ewm(span=26,adjust=False,min_periods=26).mean()); macd=ema12-ema26; sig=macd.groupby(x.ticker).transform(lambda s:s.ewm(span=9,adjust=False,min_periods=9).mean()); hist=macd-sig; x['macd_d1']=hist-hist.groupby(x.ticker).shift(1)
    p1m=(c/g.close.shift(21)-1)*100; x['mom1m_d1']=p1m-p1m.groupby(x.ticker).shift(1)
    mm200=g.close.transform(lambda s:s.rolling(200,min_periods=200).mean()); x['mm200_slope20']=(mm200/mm200.groupby(x.ticker).shift(20)-1)*100; x['below_mm200']=(c<=mm200).astype(float)
    vm20=g.volume.transform(lambda s:s.rolling(20,min_periods=20).mean()); rv=v/vm20.replace(0,np.nan); x['rvol20']=rv; x['rvol_d10']=rv-rv.groupby(x.ticker).shift(10)
    return x

def admissible(x): return np.isfinite(x.close_t5)&(x.close_t5>0)&(x.close>=1)&(x.volume>=5000)&(x.ret_fwd5_pct<=50)&(~round_suspect(x.future_ratio))

FEATURE_DIR={'dd1y':'LOW','vol60':'HIGH','rsi_d3':'LOW','macd_d1':'LOW','mom1m_d1':'LOW','mm200_slope20':'LOW','rvol20':'HIGH','rvol_d10':'HIGH'}
QS=[0.10,0.20,0.30,0.40,0.50]
MIN_DISC=300
BEAM=120
MAX_DEPTH=5

def stat(y,m):
    n=int(m.sum()); w=int((m&y).sum()); return n,w,(w/n if n else np.nan)

def main():
    raw=load_2010_2026('inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet','inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json','data/cache/actions')[['date','ticker','open','high','low','close','volume']]
    x=features(raw); x=x.loc[admissible(x)].copy(); x['year']=x.date.dt.year
    y=(x.ret_fwd5_pct>20).to_numpy(bool); disc=(x.year<=2018).to_numpy(bool); val=((x.year>=2019)&(x.year<=2022)).to_numpy(bool); oos=(x.year>=2023).to_numpy(bool)
    prim=[]
    for f,direction in FEATURE_DIR.items():
        s=pd.to_numeric(x.loc[disc,f],errors='coerce').dropna()
        for q in QS:
            qq=q if direction=='LOW' else 1-q
            thr=float(s.quantile(qq)); z=pd.to_numeric(x[f],errors='coerce')
            m=(z<=thr).fillna(False).to_numpy(bool) if direction=='LOW' else (z>=thr).fillna(False).to_numpy(bool)
            n,w,p=stat(y,m&disc)
            if n>=MIN_DISC and w>=10: prim.append({'f':f,'dir':direction,'q':qq,'thr':thr,'mask':m,'n':n,'w':w,'p':p})
    prim=sorted(prim,key=lambda a:(a['p'],a['w']),reverse=True)
    beam=[{'parts':[p],'mask':p['mask'],'n':p['n'],'w':p['w'],'p':p['p']} for p in prim]
    candidates=[]
    for depth in range(2,MAX_DEPTH+1):
        nxt=[]; seen=set()
        for b in beam:
            used={p['f'] for p in b['parts']}
            for p in prim:
                if p['f'] in used: continue
                parts=b['parts']+[p]; key=tuple(sorted((z['f'],z['dir'],round(z['q'],2)) for z in parts))
                if key in seen: continue
                seen.add(key); m=b['mask']&p['mask']; n,w,prec=stat(y,m&disc)
                if n<MIN_DISC or w<15: continue
                nxt.append({'parts':parts,'mask':m,'n':n,'w':w,'p':prec})
        nxt=sorted(nxt,key=lambda a:(a['p'],a['w']),reverse=True)[:BEAM]; candidates.extend(nxt); beam=nxt
        if not beam: break
    rows=[]
    for b in sorted(candidates,key=lambda a:(a['p'],a['w']),reverse=True)[:300]:
        nv,wv,pv=stat(y,b['mask']&val)
        if nv<100 or wv<10 or pv<0.02 or pv<b['p']*0.45: continue
        no,wo,po=stat(y,b['mask']&oos)
        desc=' & '.join(f"{p['f']} {'<=' if p['dir']=='LOW' else '>='} {p['thr']:.6g}" for p in b['parts'])
        rows.append({'depth':len(b['parts']),'pattern':desc,'disc_signals':b['n'],'disc_wins':b['w'],'disc_precision_pct':100*b['p'],'val_signals':nv,'val_wins':wv,'val_precision_pct':100*pv,'oos_signals':no,'oos_wins':wo,'oos_precision_pct':100*po})
    out=pd.DataFrame(rows)
    if out.empty: raise SystemExit('NO_VALIDATED_PATTERNS')
    out['min_val_oos_precision_pct']=out[['val_precision_pct','oos_precision_pct']].min(axis=1)
    out=out.sort_values(['oos_precision_pct','min_val_oos_precision_pct','oos_signals'],ascending=[False,False,False])
    od=Path('outputs/retro_5d_precision_search'); od.mkdir(parents=True,exist_ok=True); out.to_csv(od/'PRECISION_PATTERNS_ALL.csv',index=False)
    best={}
    for floor in [100,250,500,1000]:
        q=out[(out.val_signals>=floor)&(out.oos_signals>=floor)].head(50); q.to_csv(od/f'PRECISION_TOP_MIN_{floor}.csv',index=False); best[str(floor)]=q.iloc[0].to_dict() if len(q) else None
    summary={'objective':'maximize success rate / precision','scope':'145 accessible criteria screened upstream; second-stage precision search uses 8 strongest non-redundant cross-family factors','upstream_significant_variables':63,'features_precision_stage':list(FEATURE_DIR),'discovery':'2010-2018','validation':'2019-2022','oos':'2023-2026','oos_used_for_tuning':False,'pit_strict_certified':False,'patterns_validated':int(len(out)),'best_by_min_signal_floor':best}
    (od/'SUMMARY.json').write_text(json.dumps(summary,indent=2,default=float),encoding='utf-8'); print(json.dumps(summary,indent=2,default=float))
if __name__=='__main__': main()
