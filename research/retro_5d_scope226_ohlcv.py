from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu
from sklearn.metrics import roc_auc_score

ROUND_RATIOS = np.array([1.25,1.3333333333,1.5,1.6666666667,2.0,2.5,3.0,4.0,5.0,10.0])
LAGS = [1,3,5,10,20]
BASE_FEATURES = [
    'n_sessions','last_close','mm20','mm50','mm100','mm200','rsi14','macd','macd_signal','macd_hist',
    'atr14','bb_upper','bb_mid','bb_lower','rvol20','perf_1m_pct','perf_3m_pct','perf_6m_pct','perf_1y_pct',
    'above_mm20','above_mm50','above_mm200','volatility_20d','volatility_60d','max_drawdown_1y',
    'positive_reversal_flag','volume','ohlcv_n_sessions','ohlcv_last','ohlcv_perf_1y'
]
BINARY_FEATURES = {'above_mm20','above_mm50','above_mm200','positive_reversal_flag'}
ALIASES = {
    'ohlcv_n_sessions':'n_sessions',
    'ohlcv_last':'last_close',
    'ohlcv_perf_1y':'perf_1y_pct',
}
FAMILY = {
    'n_sessions':'DATA_DEPTH','last_close':'PRICE_LEVEL','mm20':'TREND','mm50':'TREND','mm100':'TREND','mm200':'TREND',
    'rsi14':'OSCILLATOR','macd':'MOMENTUM','macd_signal':'MOMENTUM','macd_hist':'MOMENTUM','atr14':'VOLATILITY',
    'bb_upper':'VOLATILITY','bb_mid':'TREND','bb_lower':'VOLATILITY','rvol20':'VOLUME','perf_1m_pct':'MOMENTUM',
    'perf_3m_pct':'MOMENTUM','perf_6m_pct':'MOMENTUM','perf_1y_pct':'MOMENTUM','above_mm20':'TREND',
    'above_mm50':'TREND','above_mm200':'TREND','volatility_20d':'VOLATILITY','volatility_60d':'VOLATILITY',
    'max_drawdown_1y':'DRAWDOWN','positive_reversal_flag':'REVERSAL','volume':'VOLUME','ohlcv_n_sessions':'DATA_DEPTH',
    'ohlcv_last':'PRICE_LEVEL','ohlcv_perf_1y':'MOMENTUM'
}


def round_ratio_suspect(s: pd.Series) -> pd.Series:
    x=s.to_numpy(float); out=np.zeros(len(x),dtype=bool); ok=np.isfinite(x)
    if ok.any():
        d=np.abs(x[ok,None]-ROUND_RATIOS[None,:])/ROUND_RATIOS[None,:]
        out[ok]=d.min(axis=1)<=0.005
    return pd.Series(out,index=s.index)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    x=df.sort_values(['ticker','date']).reset_index(drop=True).copy()
    x['date']=pd.to_datetime(x['date'],utc=True).dt.tz_localize(None)
    g=x.groupby('ticker',group_keys=False)
    c=pd.to_numeric(x['close'],errors='coerce'); h=pd.to_numeric(x['high'],errors='coerce'); l=pd.to_numeric(x['low'],errors='coerce')
    o=pd.to_numeric(x['open'],errors='coerce'); v=pd.to_numeric(x['volume'],errors='coerce')
    prev=g['close'].shift(1)
    x['ord_full']=g.cumcount()
    x['n_sessions']=x['ord_full']+1; x['ohlcv_n_sessions']=x['n_sessions']
    x['last_close']=c; x['ohlcv_last']=c
    for n,name in [(20,'mm20'),(50,'mm50'),(100,'mm100'),(200,'mm200')]:
        x[name]=g['close'].transform(lambda s,n=n:s.rolling(n,min_periods=n).mean())
    for n,name in [(21,'perf_1m_pct'),(63,'perf_3m_pct'),(126,'perf_6m_pct'),(252,'perf_1y_pct')]:
        x[name]=(c/g['close'].shift(n)-1)*100
    x['ohlcv_perf_1y']=x['perf_1y_pct']
    for n in [20,50,200]: x[f'above_mm{n}']=(c>x[f'mm{n}']).astype(float)
    ema12=g['close'].transform(lambda s:s.ewm(span=12,adjust=False,min_periods=12).mean())
    ema26=g['close'].transform(lambda s:s.ewm(span=26,adjust=False,min_periods=26).mean())
    x['macd']=ema12-ema26
    x['macd_signal']=x.groupby('ticker')['macd'].transform(lambda s:s.ewm(span=9,adjust=False,min_periods=9).mean())
    x['macd_hist']=x['macd']-x['macd_signal']
    delta=c-prev; gain=delta.clip(lower=0); loss=-delta.clip(upper=0)
    ag=gain.groupby(x['ticker']).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean())
    al=loss.groupby(x['ticker']).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean())
    rs=ag/al.replace(0,np.nan); x['rsi14']=100-100/(1+rs)
    tr=pd.concat([(h-l).abs(),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    atr=tr.groupby(x['ticker']).transform(lambda s:s.rolling(14,min_periods=14).mean())
    x['atr14']=atr
    std20=g['close'].transform(lambda s:s.rolling(20,min_periods=20).std(ddof=0))
    x['bb_mid']=x['mm20']; x['bb_upper']=x['mm20']+2*std20; x['bb_lower']=x['mm20']-2*std20
    vol20=g['volume'].transform(lambda s:s.rolling(20,min_periods=20).mean())
    x['rvol20']=v/vol20.replace(0,np.nan)
    dret=c/prev-1
    x['volatility_20d']=dret.groupby(x['ticker']).transform(lambda s:s.rolling(20,min_periods=20).std(ddof=0))*math.sqrt(252)*100
    x['volatility_60d']=dret.groupby(x['ticker']).transform(lambda s:s.rolling(60,min_periods=60).std(ddof=0))*math.sqrt(252)*100
    peak252=g['close'].transform(lambda s:s.rolling(252,min_periods=60).max())
    dd=(c/peak252-1)*100
    x['max_drawdown_1y']=dd.groupby(x['ticker']).transform(lambda s:s.rolling(252,min_periods=60).min())
    prev_ret=(c/prev-1)
    x['positive_reversal_flag']=((c>o)&(prev_ret<0)&(c>prev)).astype(float)
    x['volume']=v
    x['date_t5']=g['date'].shift(-5); x['close_t5']=g['close'].shift(-5)
    x['ret_fwd5_pct']=(x['close_t5']/c-1)*100; x['future_ratio']=x['close_t5']/c
    for f in BASE_FEATURES:
        if f in BINARY_FEATURES: continue
        for lag in LAGS:
            x[f'{f}_d{lag}']=x[f]-x.groupby('ticker')[f].shift(lag)
    return x


def exact_winner_episodes(x: pd.DataFrame) -> pd.DataFrame:
    hits=x.loc[x['ret_fwd5_pct']>20].copy()
    hits['prev_ord']=hits.groupby('ticker')['ord_full'].shift(1)
    hits['new_episode']=(hits['prev_ord'].isna()|((hits['ord_full']-hits['prev_ord'])>5)).astype(int)
    hits['episode_id']=hits.groupby('ticker')['new_episode'].cumsum()
    idx=hits.groupby(['ticker','episode_id'])['ret_fwd5_pct'].idxmax()
    ep=hits.loc[idx].copy()
    qa=(np.isfinite(ep['close'])&np.isfinite(ep['close_t5'])&(ep['close']>=1)&(ep['volume']>=5000)&(ep['ret_fwd5_pct']<=50)&(~round_ratio_suspect(ep['future_ratio'])))
    ep=ep.loc[qa].sort_values(['date','ticker']).reset_index(drop=True)
    if len(ep)!=5859: raise SystemExit(f'BLOCK_EXPECTED_5859_GOT_{len(ep)}')
    ep['winner_5d']=True
    return ep


def admissible_universe(x: pd.DataFrame) -> pd.DataFrame:
    valid=np.isfinite(x['close_t5'])&(x['close_t5']>0)&(x['close']>=1)&(x['volume']>=5000)&(x['ret_fwd5_pct']<=50)&(~round_ratio_suspect(x['future_ratio']))
    u=x.loc[valid].copy(); u['winner_5d']=u['ret_fwd5_pct']>20
    return u


def matched_sample(u: pd.DataFrame, ep: pd.DataFrame, n=3) -> pd.DataFrame:
    match=['last_close','volume','volatility_20d','perf_1m_pct']
    cols=['date','ticker','winner_5d','ret_fwd5_pct']+BASE_FEATURES+[f'{f}_d{lag}' for f in BASE_FEATURES if f not in BINARY_FEATURES for lag in LAGS]
    bydate={d:g for d,g in u.groupby('date',sort=False)}; rec=[]
    for _,w in ep.iterrows():
        pool=bydate.get(w['date']);
        if pool is None: continue
        pool=pool.loc[(~pool['winner_5d'])&(pool['ticker']!=w['ticker'])].copy()
        ok=np.isfinite(w[match].to_numpy(float)).all()
        if not ok or len(pool)<n: continue
        pool=pool.loc[np.isfinite(pool[match].to_numpy(float)).all(axis=1)]
        if len(pool)<n: continue
        dist=np.zeros(len(pool))
        for f in match:
            sd=float(pool[f].std(ddof=0)); sd=sd if np.isfinite(sd) and sd>1e-9 else 1.0
            dist+=((pool[f].to_numpy(float)-float(w[f]))/sd)**2
        ids=np.argpartition(dist,n-1)[:n]
        rw={c:w.get(c,np.nan) for c in cols}; rw.update({'case_id':f"{w['ticker']}|{w['date'].date()}",'role':'WINNER'}) ; rec.append(rw)
        for j,pos in enumerate(ids):
            q=pool.iloc[pos]; rr={c:q.get(c,np.nan) for c in cols}; rr.update({'case_id':rw['case_id'],'role':f'CONTROL_{j+1}'}) ; rec.append(rr)
    return pd.DataFrame(rec)


def bh(p):
    p=np.asarray(p,float); n=len(p); o=np.argsort(p); r=p[o]; q=r*n/np.arange(1,n+1); q=np.minimum.accumulate(q[::-1])[::-1]; out=np.empty(n); out[o]=np.minimum(q,1); return out


def screen(m: pd.DataFrame) -> pd.DataFrame:
    dev=m.loc[m['date'].dt.year<=2018].copy(); rows=[]
    candidates=[]
    for f in BASE_FEATURES:
        candidates.append((f,f,'LEVEL'))
        if f not in BINARY_FEATURES:
            for lag in LAGS: candidates.append((f,f'{f}_d{lag}',f'DELTA_{lag}'))
    for base,col,kind in candidates:
        a=dev.loc[dev['winner_5d'],col].astype(float).replace([np.inf,-np.inf],np.nan).dropna(); b=dev.loc[~dev['winner_5d'],col].astype(float).replace([np.inf,-np.inf],np.nan).dropna()
        if min(len(a),len(b))<50: continue
        try: p=float(mannwhitneyu(a,b,alternative='two-sided').pvalue)
        except Exception: p=1.0
        y=np.r_[np.ones(len(a)),np.zeros(len(b))]; z=np.r_[a.to_numpy(),b.to_numpy()]
        try: auc=float(roc_auc_score(y,z))
        except Exception: auc=np.nan
        sd=math.sqrt((a.var(ddof=1)+b.var(ddof=1))/2); smd=(a.mean()-b.mean())/sd if np.isfinite(sd) and sd>0 else np.nan
        rows.append({'base_criterion':base,'family':FAMILY.get(base,'OTHER'),'variable':col,'kind':kind,'winner_n':len(a),'control_n':len(b),'winner_median':a.median(),'control_median':b.median(),'winner_mean':a.mean(),'control_mean':b.mean(),'smd':smd,'auc_raw':auc,'auc_discrimination':max(auc,1-auc) if np.isfinite(auc) else np.nan,'direction':'HIGH' if a.median()>=b.median() else 'LOW','p_value':p,'alias_of':ALIASES.get(base,'')})
    out=pd.DataFrame(rows); out['q_value_bh']=bh(out['p_value'].fillna(1).to_numpy()); out['score']=(out['auc_discrimination']-0.5).abs()*2+out['smd'].abs().clip(0,3)/3
    return out.sort_values(['score','q_value_bh'],ascending=[False,True]).reset_index(drop=True)


def main():
    from v182.hebdo.meta_price_history import load_2010_2026
    raw=load_2010_2026('inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet','inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json','data/cache/actions')
    x=add_features(raw[['date','ticker','open','high','low','close','volume','segment']].copy())
    ep=exact_winner_episodes(x); u=admissible_universe(x)
    dev_ep=ep.loc[ep['date'].dt.year<=2022].copy(); matched=matched_sample(u.loc[u['date'].dt.year<=2022],dev_ep,3)
    factors=screen(matched)
    fam=(factors.groupby('family').agg(n_variables=('variable','size'),n_fdr_005=('q_value_bh',lambda s:int((s<0.05).sum())),best_score=('score','max'),best_auc=('auc_discrimination','max'),median_abs_smd=('smd',lambda s:float(np.nanmedian(np.abs(s))))).reset_index().sort_values('best_score',ascending=False))
    out=Path('outputs/retro_5d_scope226'); out.mkdir(parents=True,exist_ok=True)
    factors.to_csv(out/'FACTOR_SCREEN_SCOPE226_OHLCV.csv',index=False)
    fam.to_csv(out/'FAMILY_SUMMARY_SCOPE226_OHLCV.csv',index=False)
    ep[['date','ticker','ret_fwd5_pct']].to_csv(out/'WINNER_EPISODES_5859_LOCKED.csv',index=False)
    pd.DataFrame([{'criterion':f,'family':FAMILY.get(f,'OTHER'),'alias_of':ALIASES.get(f,''),'trajectory_tested':f not in BINARY_FEATURES} for f in BASE_FEATURES]).to_csv(out/'OHLCV_SCOPE30_REGISTRY.csv',index=False)
    summary={'winner_population_locked':len(ep),'dev_winners_2010_2022':int((ep['date'].dt.year<=2022).sum()),'oos_winners_2023_2026':int((ep['date'].dt.year>=2023).sum()),'scope226_count':226,'ohlcv_immediate_criteria':len(BASE_FEATURES),'screened_variables':len(factors),'fdr_significant_variables':int((factors['q_value_bh']<0.05).sum()),'families':fam.to_dict(orient='records'),'holdout_used_for_tuning':False,'survivorship_safe':False,'pit_strict_certified':False}
    (out/'SUMMARY_SCOPE226_OHLCV.json').write_text(json.dumps(summary,indent=2,default=float),encoding='utf-8')
    print(json.dumps(summary,indent=2,default=float))
    print(factors.head(30).to_csv(index=False))

if __name__=='__main__': main()
