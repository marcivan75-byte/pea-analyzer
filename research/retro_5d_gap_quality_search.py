from __future__ import annotations

import itertools, json
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf
from v182.hebdo.meta_price_history import load_2010_2026

OUT=Path('outputs/retro_5d_gap_quality_search'); OUT.mkdir(parents=True,exist_ok=True)
ROUND=np.array([1.25,4/3,1.5,5/3,2,2.5,3,4,5,10],float)

def split_suspect(s,tol=.005):
    a=pd.to_numeric(s,errors='coerce').to_numpy(float); out=np.zeros(len(a),bool); ok=np.isfinite(a)&(a>0)
    rr=np.r_[ROUND,1/ROUND]
    if ok.any(): out[ok]=(np.abs(a[ok,None]-rr[None,:])/rr[None,:]).min(axis=1)<=tol
    return pd.Series(out,index=s.index)

def load():
    x=load_2010_2026('inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet','inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json','data/cache/actions')[['date','ticker','open','high','low','close','volume']].copy()
    x.date=pd.to_datetime(x.date,utc=True).dt.tz_localize(None)
    for c in ['open','high','low','close','volume']: x[c]=pd.to_numeric(x[c],errors='coerce')
    x=x.dropna().sort_values(['ticker','date']).drop_duplicates(['ticker','date'],keep='last').reset_index(drop=True)
    return x

def bench(a,b):
    z=yf.download('^STOXX50E',start=(a-pd.Timedelta(days=200)).strftime('%Y-%m-%d'),end=(b+pd.Timedelta(days=10)).strftime('%Y-%m-%d'),auto_adjust=False,repair=False,progress=False,threads=False)
    if isinstance(z.columns,pd.MultiIndex): z.columns=z.columns.get_level_values(0)
    q=pd.DataFrame({'date':pd.to_datetime(z.index).tz_localize(None),'bc':pd.to_numeric(z.Close,errors='coerce').to_numpy()}).dropna()
    q['mm20']=q.bc.rolling(20,min_periods=20).mean(); q['mkt']=q.bc>q.mm20; q['br90']=q.bc.pct_change(90,fill_method=None); return q

def features(x,b):
    g=x.groupby('ticker',sort=False,group_keys=False); prev=g.close.shift(1)
    x['gap_pct']=(x.open/prev-1)*100; x['split_suspect']=split_suspect(x.open/prev)
    x['day_ret_pct']=(x.close/prev-1)*100; x['intraday_pct']=(x.close/x.open-1)*100
    rng=(x.high-x.low).replace(0,np.nan); x['close_loc']=(x.close-x.low)/rng
    x['ret5_back']=(x.close/g.close.shift(5)-1)*100; x['ret20_back']=(x.close/g.close.shift(20)-1)*100; x['ret60_back']=(x.close/g.close.shift(60)-1)*100
    x['ret90']=g.close.pct_change(90,fill_method=None)
    x['volavg20']=g.volume.transform(lambda s:s.rolling(20,min_periods=20).mean()); x['rvol']=x.volume/x.volavg20.replace(0,np.nan)
    turnover=x.close*x.volume; x['adv20']=turnover.groupby(x.ticker).transform(lambda s:s.rolling(20,min_periods=20).mean())
    x['ma20']=g.close.transform(lambda s:s.rolling(20,min_periods=20).mean()); x['ma50']=g.close.transform(lambda s:s.rolling(50,min_periods=50).mean()); x['trend20_50']=(x.close>x.ma20)&(x.ma20>x.ma50)
    d=x.close-prev; gain=d.clip(lower=0); loss=-d.clip(upper=0)
    ag=gain.groupby(x.ticker).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()); al=loss.groupby(x.ticker).transform(lambda s:s.ewm(alpha=1/14,adjust=False,min_periods=14).mean()); x['rsi14']=100-100/(1+ag/al.replace(0,np.nan))
    gg=x.groupby('ticker',sort=False,group_keys=False); x['open_j1']=gg.open.shift(-1); x['close_j5']=gg.close.shift(-5); x['ret_pit5']=(x.close_j5/x.open_j1-1)*100
    x=x.merge(b[['date','br90','mkt']],on='date',how='left'); x['rs']=(1+x.ret90)/(1+x.br90)-1; x['rs_rank']=x.groupby('date').rs.rank(pct=True)*100
    x['year']=x.date.dt.year; x['period']=np.select([x.year<=2018,x.year<=2022],['DISC','VAL'],default='OOS')
    return x

def eligible(x):
    return (x.close>=1)&(x.open>=1)&(x.volume>=5000)&(x.adv20>=800000)&(~x.split_suspect)&x.ret_pit5.between(-80,80)&x.ret_pit5.notna()

def primitives(x):
    p={}
    for v in [5,7.5,10,12.5,15,20]: p[f'GAP{v:g}']=x.gap_pct>=v
    for v in [0,2,5,10]: p[f'INTRA_GE{v:g}']=x.intraday_pct>=v
    for v in [.5,.7,.8,.9]: p[f'CLOSELOC_GE{v:g}']=x.close_loc>=v
    for v in [1.5,2,3,5]: p[f'RVOL_GE{v:g}']=x.rvol>=v
    for v in [70,80,90]: p[f'RS_GE{v}']=x.rs_rank>=v
    for v in [50,60,70]: p[f'RSI_GE{v}']=x.rsi14>=v
    for v in [5,10,20]: p[f'RET20_GE{v}']=x.ret20_back>=v
    p['TREND20_50']=x.trend20_50.fillna(False); p['MKT']=x.mkt.fillna(False)
    return p

def stat(x,m,period):
    z=x.loc[m&(x.period==period),'ret_pit5'].dropna(); n=len(z); w=int((z>=20).sum())
    return n,w,(100*w/n if n else np.nan),(z.mean() if n else np.nan),(z.median() if n else np.nan)

def main():
    raw=load(); x=features(raw,bench(raw.date.min(),raw.date.max())); e=eligible(x); P=primitives(x)
    base={p:stat(x,e,p) for p in ['DISC','VAL','OOS']}
    rows=[]
    # Singles + combinations of 2 and 3, always including a gap primitive, fixed candidate library.
    gap_names=[k for k in P if k.startswith('GAP')]
    other=[k for k in P if not k.startswith('GAP')]
    defs=[]
    defs += [(g,) for g in gap_names]
    defs += [(g,o) for g in gap_names for o in other]
    # limit 3-way to distinct feature families by prefix to reduce multiple testing/redundancy
    def fam(k): return k.split('_')[0].split('GE')[0].rstrip('0123456789.')
    for g in gap_names:
        for a,b in itertools.combinations(other,2):
            if fam(a)==fam(b): continue
            defs.append((g,a,b))
    for parts in defs:
        m=e.copy()
        for k in parts: m &= P[k]
        rec={'pattern':'+'.join(parts),'depth':len(parts)}
        valid=True
        for per in ['DISC','VAL','OOS']:
            n,w,pr,mean,med=stat(x,m,per); rec[f'{per}_n']=n; rec[f'{per}_wins']=w; rec[f'{per}_precision']=pr; rec[f'{per}_mean']=mean; rec[f'{per}_median']=med
        # Selection is based on DISC+VAL only; OOS columns are reported but never used to admit candidates.
        if rec['DISC_n']>=50 and rec['VAL_n']>=50 and rec['DISC_wins']>=3 and rec['VAL_wins']>=3 and rec['DISC_precision']>=2 and rec['VAL_precision']>=2 and rec['DISC_mean']>-1.0 and rec['VAL_mean']>-1.0:
            rows.append(rec)
    out=pd.DataFrame(rows)
    if len(out):
        out['robust_pre_oos']=out[['DISC_precision','VAL_precision']].min(axis=1)
        out=out.sort_values(['robust_pre_oos','VAL_precision','DISC_n'],ascending=[False,False,False])
    out.to_csv(OUT/'VALIDATED_GAP_QUALITY_PATTERNS.csv',index=False)
    # all single gap thresholds for context
    base_rows=[]
    for g in gap_names:
        m=e&P[g]
        r={'pattern':g}
        for per in ['DISC','VAL','OOS']:
            n,w,pr,mean,med=stat(x,m,per); r[f'{per}_n']=n; r[f'{per}_wins']=w; r[f'{per}_precision']=pr; r[f'{per}_mean']=mean
        base_rows.append(r)
    pd.DataFrame(base_rows).to_csv(OUT/'GAP_THRESHOLDS.csv',index=False)
    summary={'eligible':int(e.sum()),'base_rates_pct':{p:base[p][2] for p in base},'candidate_library':len(defs),'validated_pre_oos':int(len(out)),'selection_rule':'DISC and VAL each n>=50,wins>=3,precision>=2%,mean>-1%; OOS not used for selection','top_pre_oos':out.head(20).to_dict('records') if len(out) else []}
    (OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf-8'); print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
