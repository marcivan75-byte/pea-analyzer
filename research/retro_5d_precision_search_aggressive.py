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

def rsi_wilder(c,t,n=14):
    prev=c.groupby(t).shift(1); d=c-prev; gain=d.clip(lower=0); loss=-d.clip(upper=0)
    ag=gain.groupby(t).transform(lambda s:s.ewm(alpha=1/n,adjust=False,min_periods=n).mean())
    al=loss.groupby(t).transform(lambda s:s.ewm(alpha=1/n,adjust=False,min_periods=n).mean())
    return 100-100/(1+ag/al.replace(0,np.nan))

def features(df):
    x=df.sort_values(['ticker','date']).reset_index(drop=True).copy(); x['date']=pd.to_datetime(x.date,utc=True).dt.tz_localize(None)
    t=x.ticker; g=x.groupby('ticker',group_keys=False)
    c=pd.to_numeric(x.close,errors='coerce'); o=pd.to_numeric(x.open,errors='coerce'); h=pd.to_numeric(x.high,errors='coerce'); l=pd.to_numeric(x.low,errors='coerce'); v=pd.to_numeric(x.volume,errors='coerce')
    prev=g.close.shift(1); dr=c/prev-1
    x['close_t5']=g.close.shift(-5); x['ret_fwd5_pct']=(x.close_t5/c-1)*100; x['future_ratio']=x.close_t5/c
    for n in [1,3,5,10,20,60,126,252]: x[f'ret{n}']=(c/g.close.shift(n)-1)*100
    for n in [10,20,50,100,200]:
        ma=g.close.transform(lambda s,n=n:s.rolling(n,min_periods=n).mean()); x[f'dist_ma{n}']=(c/ma-1)*100
        if n in [20,50,200]: x[f'ma{n}_slope10']=(ma/ma.groupby(t).shift(10)-1)*100
    rsi=rsi_wilder(c,t,14); x['rsi14']=rsi; x['rsi_d3']=rsi-rsi.groupby(t).shift(3); x['rsi_d5']=rsi-rsi.groupby(t).shift(5)
    low14=g.low.transform(lambda s:s.rolling(14,min_periods=14).min()); high14=g.high.transform(lambda s:s.rolling(14,min_periods=14).max())
    stoch=100*(c-low14)/(high14-low14).replace(0,np.nan); sd=stoch.groupby(t).transform(lambda s:s.rolling(3,min_periods=3).mean())
    x['stoch_k']=stoch; x['stoch_d']=sd; x['stoch_gap']=stoch-sd
    ema12=g.close.transform(lambda s:s.ewm(span=12,adjust=False,min_periods=12).mean()); ema26=g.close.transform(lambda s:s.ewm(span=26,adjust=False,min_periods=26).mean()); macd=ema12-ema26
    sig=macd.groupby(t).transform(lambda s:s.ewm(span=9,adjust=False,min_periods=9).mean()); hist=macd-sig
    x['macd_hist_pct']=100*hist/c; x['macd_d1']=hist-hist.groupby(t).shift(1); x['macd_d3']=hist-hist.groupby(t).shift(3)
    tr=pd.concat([(h-l).abs(),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    atr14=tr.groupby(t).transform(lambda s:s.rolling(14,min_periods=14).mean()); x['atr14_pct']=100*atr14/c
    for n in [10,20,60]: x[f'vol{n}']=dr.groupby(t).transform(lambda s,n=n:s.rolling(n,min_periods=n).std(ddof=0))*math.sqrt(252)*100
    ma20=g.close.transform(lambda s:s.rolling(20,min_periods=20).mean()); sd20=g.close.transform(lambda s:s.rolling(20,min_periods=20).std(ddof=0)); x['bb_width20']=100*4*sd20/ma20; x['bb_z20']=(c-ma20)/sd20.replace(0,np.nan)
    for n in [10,20,60]:
        vm=g.volume.transform(lambda s,n=n:s.rolling(n,min_periods=n).mean()); x[f'rvol{n}']=v/vm.replace(0,np.nan)
    x['rvol_d5']=x.rvol20-x.rvol20.groupby(t).shift(5); x['rvol_d10']=x.rvol20-x.rvol20.groupby(t).shift(10)
    peak60=g.close.transform(lambda s:s.rolling(60,min_periods=20).max()); peak252=g.close.transform(lambda s:s.rolling(252,min_periods=60).max())
    low60=g.close.transform(lambda s:s.rolling(60,min_periods=20).min())
    x['dd60']=(c/peak60-1)*100; x['dd1y']=(c/peak252-1)*100; x['dist_low60']=(c/low60-1)*100
    x['gap_pct']=(o/prev-1)*100; x['close_loc']=100*(c-l)/(h-l).replace(0,np.nan); x['range_pct']=100*(h-l)/c
    x['up_days5']=(dr>0).groupby(t).transform(lambda s:s.rolling(5,min_periods=5).sum()); x['reversal5']=x.ret1-x.ret5/5
    x['mom_acc_5_20']=x.ret5-x.ret20/4; x['mom_acc_10_20']=x.ret10-x.ret20/2
    return x

def admissible(x): return np.isfinite(x.close_t5)&(x.close_t5>0)&(x.close>=1)&(x.volume>=5000)&(x.ret_fwd5_pct<=50)&(~round_suspect(x.future_ratio))

FEATURES=['ret1','ret3','ret5','ret10','ret20','ret60','ret126','ret252','mom_acc_5_20','mom_acc_10_20','dist_ma10','dist_ma20','dist_ma50','dist_ma100','dist_ma200','ma20_slope10','ma50_slope10','ma200_slope10','rsi14','rsi_d3','rsi_d5','stoch_k','stoch_gap','macd_hist_pct','macd_d1','macd_d3','atr14_pct','vol10','vol20','vol60','bb_width20','bb_z20','rvol10','rvol20','rvol60','rvol_d5','rvol_d10','dd60','dd1y','dist_low60','gap_pct','close_loc','range_pct','up_days5','reversal5']
QS=[0.05,0.10,0.15,0.20,0.25,0.30,0.40,0.50,0.60,0.70,0.75,0.80,0.85,0.90,0.95]
MIN_DISC=50; MIN_WINS=5; BEAM=60; MAX_DEPTH=5

def stat(y,m):
    n=int(m.sum()); w=int((m&y).sum()); return n,w,(w/n if n else np.nan)

def main():
    raw=load_2010_2026('inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet','inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json','data/cache/actions')[['date','ticker','open','high','low','close','volume']]
    x=features(raw); x=x.loc[admissible(x)].copy(); x['year']=x.date.dt.year
    y=(x.ret_fwd5_pct>20).to_numpy(bool); disc=(x.year<=2018).to_numpy(bool); val=((x.year>=2019)&(x.year<=2022)).to_numpy(bool); oos=(x.year>=2023).to_numpy(bool)
    prim=[]
    for f in FEATURES:
        z=pd.to_numeric(x[f],errors='coerce'); sd=z[disc].dropna()
        if len(sd)<1000: continue
        for q in QS:
            thr=float(sd.quantile(q))
            for op in ['LE','GE']:
                m=(z<=thr).fillna(False).to_numpy(bool) if op=='LE' else (z>=thr).fillna(False).to_numpy(bool)
                n,w,p=stat(y,m&disc)
                if n>=MIN_DISC and w>=MIN_WINS: prim.append({'f':f,'op':op,'q':q,'thr':thr,'mask':m,'n':n,'w':w,'p':p})
    prim=sorted(prim,key=lambda a:(a['p'],a['w']),reverse=True)
    # runtime control: at most 8 threshold variants per feature
    reduced=[]; counts={}
    for p in prim:
        if counts.get(p['f'],0)>=8: continue
        reduced.append(p); counts[p['f']]=counts.get(p['f'],0)+1
    prim=reduced
    beam=[{'parts':[p],'mask':p['mask'],'n':p['n'],'w':p['w'],'p':p['p']} for p in prim[:240]]
    candidates=[]
    for depth in range(2,MAX_DEPTH+1):
        nxt=[]; seen=set()
        for b in beam:
            used={p['f'] for p in b['parts']}
            for p in prim[:240]:
                if p['f'] in used: continue
                key=tuple(sorted((z['f'],z['op'],round(z['q'],2)) for z in b['parts']+[p]))
                if key in seen: continue
                seen.add(key); m=b['mask']&p['mask']; n,w,prec=stat(y,m&disc)
                if n<MIN_DISC or w<MIN_WINS: continue
                score=prec*(1+0.08*math.log10(max(n,10)))
                nxt.append({'parts':b['parts']+[p],'mask':m,'n':n,'w':w,'p':prec,'score':score})
        nxt=sorted(nxt,key=lambda a:(a['score'],a['w']),reverse=True)[:BEAM]
        candidates.extend(nxt); beam=nxt
        if not beam: break
    rows=[]
    for b in sorted(candidates,key=lambda a:(a['p'],a['w']),reverse=True)[:800]:
        nv,wv,pv=stat(y,b['mask']&val)
        if nv<30 or wv<3: continue
        if pv<0.025 or pv<b['p']*0.30: continue
        no,wo,po=stat(y,b['mask']&oos)
        desc=' & '.join(f"{p['f']} {'<=' if p['op']=='LE' else '>='} {p['thr']:.6g}" for p in b['parts'])
        rows.append({'depth':len(b['parts']),'pattern':desc,'disc_signals':b['n'],'disc_wins':b['w'],'disc_precision_pct':100*b['p'],'val_signals':nv,'val_wins':wv,'val_precision_pct':100*pv,'oos_signals':no,'oos_wins':wo,'oos_precision_pct':100*po,'selection_score':100*min(b['p'],pv)})
    out=pd.DataFrame(rows)
    if out.empty: raise SystemExit('NO_VALIDATED_PATTERNS')
    out=out.sort_values(['selection_score','val_precision_pct','val_signals'],ascending=[False,False,False]).reset_index(drop=True)
    od=Path('outputs/retro_5d_precision_aggressive'); od.mkdir(parents=True,exist_ok=True); out.to_csv(od/'ALL_VALIDATED_PATTERNS.csv',index=False)
    best={}
    for floor in [30,50,100,250,500]:
        q=out[(out.val_signals>=floor)].head(100).copy(); q.to_csv(od/f'TOP_PRE_OOS_MIN_{floor}.csv',index=False)
        best[str(floor)]=q.iloc[0].to_dict() if len(q) else None
    summary={'objective':'maximize success rate / precision','scope':'runtime-optimized aggressive search over 45 accessible OHLCV-derived factors','discovery':'2010-2018','validation':'2019-2022','oos':'2023-2026','oos_used_for_tuning':False,'pit_strict_certified':False,'features_count':len(FEATURES),'beam':BEAM,'max_depth':MAX_DEPTH,'validated_patterns':int(len(out)),'best_frozen_by_validation_floor':best}
    (od/'SUMMARY.json').write_text(json.dumps(summary,indent=2,default=float),encoding='utf-8'); print(json.dumps(summary,indent=2,default=float))

if __name__=='__main__': main()
