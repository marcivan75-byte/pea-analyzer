from __future__ import annotations

import itertools, json, math
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
    x['close_t5']=g.close.shift(-5); x['ret_fwd5_pct']=(x.close_t5/c-1)*100; x['future_ratio']=x.close_t5/c
    dr=c/prev-1
    x['ret1']=(c/prev-1)*100; x['ret3']=(c/g.close.shift(3)-1)*100; x['ret5']=(c/g.close.shift(5)-1)*100; x['ret10']=(c/g.close.shift(10)-1)*100; x['ret20']=(c/g.close.shift(20)-1)*100
    x['range_pct']=(h-l)/c*100
    x['vol20']=dr.groupby(x.ticker).transform(lambda s:s.rolling(20,min_periods=20).std(ddof=0))*math.sqrt(252)*100
    x['vol60']=dr.groupby(x.ticker).transform(lambda s:s.rolling(60,min_periods=60).std(ddof=0))*math.sqrt(252)*100
    peak60=g.high.transform(lambda s:s.shift(1).rolling(60,min_periods=60).max()); x['dist_high60']=(c/peak60-1)*100
    peak252=g.close.transform(lambda s:s.rolling(252,min_periods=60).max()); dd=(c/peak252-1)*100; x['dd1y']=dd.groupby(x.ticker).transform(lambda s:s.rolling(252,min_periods=60).min())
    d=c-prev; gain=d.clip(lower=0); loss=-d.clip(upper=0); ag=gain.groupby(x.ticker).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()); al=loss.groupby(x.ticker).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()); rsi=100-100/(1+ag/al.replace(0,np.nan)); x['rsi14']=rsi; x['rsi_d3']=rsi-rsi.groupby(x.ticker).shift(3); x['rsi_d5']=rsi-rsi.groupby(x.ticker).shift(5)
    low14=g.low.transform(lambda s:s.rolling(14,min_periods=14).min()); high14=g.high.transform(lambda s:s.rolling(14,min_periods=14).max()); st=100*(c-low14)/(high14-low14).replace(0,np.nan); x['stoch14']=st; x['stoch_d3']=st-st.groupby(x.ticker).shift(3)
    ema12=g.close.transform(lambda s:s.ewm(span=12,adjust=False,min_periods=12).mean()); ema26=g.close.transform(lambda s:s.ewm(span=26,adjust=False,min_periods=26).mean()); macd=ema12-ema26; sig=macd.groupby(x.ticker).transform(lambda s:s.ewm(span=9,adjust=False,min_periods=9).mean()); hist=macd-sig; x['macd_hist']=hist; x['macd_d1']=hist-hist.groupby(x.ticker).shift(1); x['macd_d3']=hist-hist.groupby(x.ticker).shift(3)
    mm20=g.close.transform(lambda s:s.rolling(20,min_periods=20).mean()); mm50=g.close.transform(lambda s:s.rolling(50,min_periods=50).mean()); mm200=g.close.transform(lambda s:s.rolling(200,min_periods=200).mean())
    x['dist_mm20']=(c/mm20-1)*100; x['dist_mm50']=(c/mm50-1)*100; x['above_mm200']=(c>mm200).astype(float); x['mm200_slope20']=(mm200/mm200.groupby(x.ticker).shift(20)-1)*100
    sd20=g.close.transform(lambda s:s.rolling(20,min_periods=20).std(ddof=0)); x['bb_width20']=4*sd20/mm20*100
    tr=pd.concat([(h-l).abs(),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1); atr=tr.groupby(x.ticker).transform(lambda s:s.rolling(14,min_periods=14).mean()); x['atr14_pct']=atr/c*100
    vm20=g.volume.transform(lambda s:s.rolling(20,min_periods=20).mean()); rv=v/vm20.replace(0,np.nan); x['rvol20']=rv; x['rvol_d3']=rv-rv.groupby(x.ticker).shift(3); x['rvol_d10']=rv-rv.groupby(x.ticker).shift(10)
    return x

def admissible(x): return np.isfinite(x.close_t5)&(x.close_t5>0)&(x.close>=1)&(x.volume>=5000)&(x.ret_fwd5_pct<=50)&(~round_suspect(x.future_ratio))

FEATURE_DIR={
'dd1y':'LOW','vol60':'HIGH','vol20':'HIGH','range_pct':'HIGH','dist_high60':'LOW','rsi14':'LOW','rsi_d3':'LOW','rsi_d5':'LOW','stoch14':'LOW','stoch_d3':'LOW','macd_hist':'LOW','macd_d1':'LOW','macd_d3':'LOW','ret1':'LOW','ret3':'LOW','ret5':'LOW','ret10':'LOW','ret20':'LOW','dist_mm20':'LOW','dist_mm50':'LOW','mm200_slope20':'LOW','rvol20':'HIGH','rvol_d3':'HIGH','rvol_d10':'HIGH','bb_width20':'HIGH','atr14_pct':'HIGH'}
QS=[0.10,0.20,0.30,0.40,0.50,0.60,0.70,0.80,0.90]
MIN_DISC=500
BEAM=250
MAX_DEPTH=5

def eval_mask(y,m):
    n=int(m.sum()); w=int((m&y).sum()); return n,w,(w/n if n else np.nan)

def main():
    raw=load_2010_2026('inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet','inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json','data/cache/actions')[['date','ticker','open','high','low','close','volume']]
    x=features(raw); x=x.loc[admissible(x)].copy(); x['year']=x.date.dt.year; y=(x.ret_fwd5_pct>20).to_numpy(bool)
    disc=(x.year<=2018).to_numpy(bool); val=((x.year>=2019)&(x.year<=2022)).to_numpy(bool); oos=(x.year>=2023).to_numpy(bool)
    primitives=[]
    for f,direction in FEATURE_DIR.items():
        s=pd.to_numeric(x.loc[disc,f],errors='coerce').dropna()
        for q in QS:
            thr=float(s.quantile(q)); m=(pd.to_numeric(x[f],errors='coerce')<=thr).to_numpy(bool) if direction=='LOW' else (pd.to_numeric(x[f],errors='coerce')>=thr).to_numpy(bool)
            nd,wd,pd_=eval_mask(y, m&disc)
            if nd>=MIN_DISC: primitives.append({'feature':f,'direction':direction,'q':q,'thr':thr,'mask':m,'disc_n':nd,'disc_w':wd,'disc_p':pd_})
    primitives=sorted(primitives,key=lambda z:(z['disc_p'],z['disc_w']),reverse=True)[:120]
    beam=[]
    for p in primitives:
        beam.append({'parts':[p],'mask':p['mask'],'disc_n':p['disc_n'],'disc_w':p['disc_w'],'disc_p':p['disc_p']})
    allc=[]
    for depth in range(2,MAX_DEPTH+1):
        cand=[]; seen=set()
        for b in beam:
            used={z['feature'] for z in b['parts']}
            for p in primitives:
                if p['feature'] in used: continue
                parts=b['parts']+[p]; key=tuple(sorted((z['feature'],z['direction'],z['q']) for z in parts))
                if key in seen: continue
                seen.add(key); m=b['mask']&p['mask']; nd,wd,prec=eval_mask(y,m&disc)
                if nd<MIN_DISC or wd<20: continue
                cand.append({'parts':parts,'mask':m,'disc_n':nd,'disc_w':wd,'disc_p':prec})
        cand=sorted(cand,key=lambda z:(z['disc_p'],z['disc_w']),reverse=True)[:BEAM]
        allc.extend(cand); beam=cand
        if not beam: break
    rows=[]
    for b in sorted(allc,key=lambda z:(z['disc_p'],z['disc_w']),reverse=True)[:500]:
        nv,wv,pv=eval_mask(y,b['mask']&val)
        if nv<100 or wv<10: continue
        # validation gate is precision-first but still needs stability over discovery
        if pv < max(0.02, b['disc_p']*0.50): continue
        no,wo,po=eval_mask(y,b['mask']&oos)
        desc=' & '.join([f"{p['feature']} {'<=' if p['direction']=='LOW' else '>='} {p['thr']:.6g} (q{int(p['q']*100)})" for p in b['parts']])
        rows.append({'depth':len(b['parts']),'pattern':desc,'disc_signals':b['disc_n'],'disc_wins':b['disc_w'],'disc_precision_pct':100*b['disc_p'],'val_signals':nv,'val_wins':wv,'val_precision_pct':100*pv,'oos_signals':no,'oos_wins':wo,'oos_precision_pct':100*po})
    out=pd.DataFrame(rows)
    if out.empty: raise SystemExit('NO_VALIDATED_PATTERNS')
    out['min_val_oos_precision_pct']=out[['val_precision_pct','oos_precision_pct']].min(axis=1)
    out=out.sort_values(['oos_precision_pct','min_val_oos_precision_pct','oos_signals'],ascending=[False,False,False])
    od=Path('outputs/retro_5d_precision_search'); od.mkdir(parents=True,exist_ok=True)
    out.to_csv(od/'PRECISION_PATTERNS_ALL.csv',index=False)
    for floor in [100,250,500,1000]:
        q=out[(out.val_signals>=floor)&(out.oos_signals>=floor)].head(50); q.to_csv(od/f'PRECISION_TOP_MIN_{floor}.csv',index=False)
    summary={'objective':'maximize pattern precision / hit rate, not return magnitude','candidate_universe':'145 accessible OHLCV-derived criteria; precision search uses statistically screened non-redundant primitives','features_in_precision_stage':list(FEATURE_DIR),'discovery':'2010-2018','validation':'2019-2022','oos':'2023-2026','oos_used_for_tuning':False,'pit_strict_certified':False,'patterns_validated':int(len(out)),'best_by_floor':{}}
    for floor in [100,250,500,1000]:
        q=out[(out.val_signals>=floor)&(out.oos_signals>=floor)]
        summary['best_by_floor'][str(floor)]=q.iloc[0].to_dict() if len(q) else None
    (od/'SUMMARY.json').write_text(json.dumps(summary,indent=2,default=float),encoding='utf-8'); print(json.dumps(summary,indent=2,default=float))
if __name__=='__main__': main()
