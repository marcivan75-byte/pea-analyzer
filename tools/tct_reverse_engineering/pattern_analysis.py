from __future__ import annotations

from pathlib import Path
import json
import numpy as np
import pandas as pd

OUT=Path('outputs/tct_reverse_engineering_patterns_v1')
INV=Path('outputs/tct_reverse_engineering_v1')
LAGS=(0,1,3,5)
POS_THRESH=25.0
SCALE_BREAK=10.0
RNG=np.random.default_rng(20260903)


def norm(x):
    return str(x).strip().lower().replace(' ','_').replace('-','_')

def naive(x):
    return pd.to_datetime(x,utc=True,errors='coerce').dt.tz_convert(None)

def adjust_ohlcv(d):
    d=d.copy(); d.columns=[norm(c) for c in d.columns]
    ren={'adjclose':'adj_close','datetime':'date','timestamp':'date','symbol':'ticker'}
    d=d.rename(columns=ren)
    if 'date' not in d and isinstance(d.index,pd.DatetimeIndex):
        d=d.reset_index().rename(columns={'index':'date'})
    if 'adj_close' in d and 'close' in d:
        f=pd.to_numeric(d['adj_close'],errors='coerce')/pd.to_numeric(d['close'],errors='coerce').replace(0,np.nan)
        for c in ('open','high','low','close'):
            if c in d: d[c]=pd.to_numeric(d[c],errors='coerce')*f
        d['close']=pd.to_numeric(d['adj_close'],errors='coerce')
    elif 'close' in d:
        for c in ('open','high','low','close'):
            if c in d: d[c]=pd.to_numeric(d[c],errors='coerce')
    else: return pd.DataFrame()
    for c in ('open','high','low'):
        if c not in d: d[c]=d['close']
    if 'volume' not in d: d['volume']=np.nan
    keep=['date','ticker','open','high','low','close','volume']
    if 'ticker' not in d: return pd.DataFrame()
    d=d[keep]
    d['date']=naive(d['date']); d['ticker']=d['ticker'].astype(str).str.upper()
    for c in ('open','high','low','close','volume'): d[c]=pd.to_numeric(d[c],errors='coerce')
    return d

def read_pre():
    return adjust_ohlcv(pd.read_parquet('inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet'))

def read_post():
    parts=[]
    for p in sorted(Path('data/cache/actions').glob('history_*.parquet')):
        raw=pd.read_parquet(p)
        if isinstance(raw.columns,pd.MultiIndex):
            fields={'open','high','low','close','adj_close','adjclose','adj close','volume'}
            scores=[]
            for lvl in range(raw.columns.nlevels):
                vals={norm(v) for v in raw.columns.get_level_values(lvl)}
                scores.append(len(vals & {norm(v) for v in fields}))
            fl=int(np.argmax(scores)); tl=1-fl if raw.columns.nlevels==2 else next(i for i in range(raw.columns.nlevels) if i!=fl)
            for ticker in pd.Index(raw.columns.get_level_values(tl)).unique():
                cols=raw.columns[raw.columns.get_level_values(tl)==ticker]
                sub=raw.loc[:,cols].copy(); sub.columns=[norm(c[fl]) for c in cols]
                sub=sub.loc[:,~pd.Index(sub.columns).duplicated(keep='last')]
                sub=sub.reset_index(); sub=sub.rename(columns={sub.columns[0]:'date'}); sub['ticker']=str(ticker).upper()
                a=adjust_ohlcv(sub)
                if not a.empty: parts.append(a[a.date>=pd.Timestamp('2023-01-01')])
        else:
            d=raw.copy()
            if 'ticker' not in [norm(c) for c in d.columns]: d['ticker']=p.stem.replace('history_','')
            a=adjust_ohlcv(d)
            if not a.empty: parts.append(a[a.date>=pd.Timestamp('2023-01-01')])
    return pd.concat(parts,ignore_index=True,sort=False) if parts else pd.DataFrame()

def rsi(s,n=14):
    x=s.diff(); up=x.clip(lower=0).rolling(n,min_periods=n).mean(); dn=(-x.clip(upper=0)).rolling(n,min_periods=n).mean()
    return 100-100/(1+up/dn.replace(0,np.nan))

def add_features(g):
    g=g.sort_values('date').copy(); c=g.close; h=g.high; l=g.low; o=g.open; v=g.volume
    ret=c.pct_change(); g['ret1']=ret
    for n in (2,3,5,10,20,40,60,120,252): g[f'ret{n}']=c.pct_change(n)
    for n in (5,10,20,50,100,200):
        sma=c.rolling(n,min_periods=n).mean(); g[f'px_sma{n}']=c/sma-1; g[f'sma{n}_slope5']=sma.pct_change(5)
    for n in (5,10,20,50):
        ema=c.ewm(span=n,adjust=False,min_periods=n).mean(); g[f'px_ema{n}']=c/ema-1; g[f'ema{n}_slope5']=ema.pct_change(5)
    g['ma_align_5_20_50']=((c>c.rolling(5).mean())&(c>c.rolling(20).mean())&(c>c.rolling(50).mean())).astype(float)
    g['sma20_gt_sma50']=(c.rolling(20).mean()>c.rolling(50).mean()).astype(float)
    for n in (7,14,21): g[f'rsi{n}']=rsi(c,n)
    ema12=c.ewm(span=12,adjust=False,min_periods=26).mean(); ema26=c.ewm(span=26,adjust=False,min_periods=26).mean(); macd=ema12-ema26; sig=macd.ewm(span=9,adjust=False,min_periods=9).mean()
    g['macd_pct']=macd/c; g['macd_hist_pct']=(macd-sig)/c; g['macd_hist_d3']=g['macd_hist_pct'].diff(3)
    for n in (14,28):
        lo=l.rolling(n,min_periods=n).min(); hi=h.rolling(n,min_periods=n).max(); g[f'sto{n}']=100*(c-lo)/(hi-lo).replace(0,np.nan)
    prev=c.shift(1); tr=pd.concat([(h-l),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    for n in (5,10,14,20,60):
        g[f'vol{n}']=ret.rolling(n,min_periods=n).std()*np.sqrt(252)
        g[f'atr{n}_pct']=tr.rolling(n,min_periods=n).mean()/c
    g['vol5_over20']=g['vol5']/g['vol20']; g['vol10_over60']=g['vol10']/g['vol60']
    g['daily_range_pct']=(h-l)/prev; g['body_pct']=(c-o)/o.replace(0,np.nan); g['gap_pct']=o/prev-1
    g['close_location']=(c-l)/(h-l).replace(0,np.nan)
    g['upper_wick_pct']=(h-pd.concat([o,c],axis=1).max(axis=1))/prev
    g['lower_wick_pct']=(pd.concat([o,c],axis=1).min(axis=1)-l)/prev
    for n in (20,50):
        m=c.rolling(n,min_periods=n).mean(); sd=c.rolling(n,min_periods=n).std(); g[f'bb_z{n}']=(c-m)/sd; g[f'bb_width{n}']=4*sd/m
    for n in (20,60,120,252):
        rh=h.shift(1).rolling(n,min_periods=n).max(); rl=l.shift(1).rolling(n,min_periods=n).min()
        g[f'dist_high{n}']=c/rh-1; g[f'dist_low{n}']=c/rl-1; g[f'range_pos{n}']=(c-rl)/(rh-rl).replace(0,np.nan)
        g[f'breakout_high{n}']=(c>rh).astype(float)
    peak=c.cummax(); g['drawdown_from_ath']=c/peak-1
    dollar=c*v
    for n in (5,10,20,60):
        vm=v.rolling(n,min_periods=n).mean(); g[f'rvol{n}']=v/vm; g[f'vol_z{n}']=(v-vm)/v.rolling(n,min_periods=n).std()
        g[f'dollarvol_med{n}']=dollar.rolling(n,min_periods=n).median()
    g['volume_ret5']=v.pct_change(5); g['volume_ret20']=v.pct_change(20); g['rvol5_over20']=g['rvol5']/g['rvol20']
    direction=np.sign(c.diff()).fillna(0); obv=(direction*v.fillna(0)).cumsum(); vm20=v.rolling(20,min_periods=20).mean()
    g['obv_slope5']=obv.diff(5)/vm20; g['obv_slope20']=obv.diff(20)/vm20
    g['amihud20']=(ret.abs()/dollar.replace(0,np.nan)).rolling(20,min_periods=20).mean()
    g['mom_accel_5_20']=g['ret5']-g['ret20']/4; g['mom_accel_10_60']=g['ret10']-g['ret60']/6
    g['atr_compress_5_20']=g['atr5_pct']/g['atr20_pct']; g['bb_width20_chg5']=g['bb_width20'].pct_change(5)
    return g

def future_mfe(c,H=20):
    a=c.to_numpy(float); out=np.full(len(a),np.nan)
    for k in range(1,H+1):
        z=np.full(len(a),np.nan); z[:-k]=a[k:]/a[:-k]-1
        out=np.fmax(out,z)
    out[len(a)-H:]=np.nan
    return out

def build_samples(allp,master):
    pos={(str(r.ticker).upper(),pd.Timestamp(r.j0)):r for r in master.itertuples() if float(r.mfe20_pct)>POS_THRESH}
    rows=[]
    for ticker,g0 in allp.groupby('ticker',sort=False):
        g=add_features(g0); g['future_mfe20']=future_mfe(g.close,20)*100; g['year']=g.date.dt.year
        date_to_i={pd.Timestamp(d):i for i,d in enumerate(g.date)}
        pos_i=sorted(date_to_i[d] for (t,d),r in pos.items() if t==ticker and d in date_to_i)
        blocked=np.zeros(len(g),bool)
        for i in pos_i: blocked[max(0,i-25):min(len(g),i+26)]=True
        candidate=[(i,1,'POS') for i in pos_i]
        for year in sorted(set(g.year.dropna().astype(int))):
            pi=[i for i in pos_i if int(g.year.iloc[i])==year]
            if not pi: continue
            eligible=np.flatnonzero((g.year.to_numpy()==year)&(~blocked)&np.isfinite(g.future_mfe20.to_numpy())&(np.arange(len(g))>=252))
            broad=eligible[g.future_mfe20.to_numpy()[eligible] < POS_THRESH]
            hard=eligible[(g.future_mfe20.to_numpy()[eligible] >=10)&(g.future_mfe20.to_numpy()[eligible] < POS_THRESH)]
            for pool,label,mult in ((broad,'CTRL',2),(hard,'HARD',1)):
                if len(pool):
                    take=RNG.choice(pool,size=min(len(pool),len(pi)*mult),replace=False)
                    candidate += [(int(i),0,label) for i in take]
        base_exclude={'date','ticker','open','high','low','close','volume','future_mfe20','year'}
        fcols=[c for c in g.columns if c not in base_exclude]
        for i,y,kind in candidate:
            if i<252 or i+20>=len(g): continue
            rec={'ticker':ticker,'date':g.date.iloc[i],'year':int(g.year.iloc[i]),'target':y,'sample_type':kind,'future_mfe20_pct':float(g.future_mfe20.iloc[i])}
            if y==1:
                rr=pos.get((ticker,pd.Timestamp(g.date.iloc[i]))); rec['winner_mfe20_pct']=float(rr.mfe20_pct) if rr is not None else np.nan
                rec['winner_h4']=bool(rr.mfe_h4_pct>POS_THRESH) if rr is not None else False; rec['winner_h10']=bool(rr.mfe_h10_pct>POS_THRESH) if rr is not None else False
            for lag in LAGS:
                j=i-lag
                for c in fcols: rec[f'{c}_L{lag}']=g[c].iloc[j]
            rows.append(rec)
    return pd.DataFrame(rows)

def auc_rank(y,x):
    m=np.isfinite(x); y=np.asarray(y)[m]; x=np.asarray(x)[m]
    if len(np.unique(y))<2:return np.nan
    r=pd.Series(x).rank(method='average').to_numpy(); n1=y.sum(); n0=len(y)-n1
    return float((r[y==1].sum()-n1*(n1+1)/2)/(n1*n0))

def univariate(samples,years):
    d=samples[samples.year.isin(years)&samples.sample_type.isin(['POS','CTRL'])].copy(); y=d.target.to_numpy(int)
    rows=[]
    for c in d.columns:
        if c.endswith(tuple(f'_L{x}' for x in LAGS)):
            x=pd.to_numeric(d[c],errors='coerce').to_numpy(float); m=np.isfinite(x)
            if m.sum()<200: continue
            a=auc_rank(y,x); med1=np.nanmedian(x[y==1]); med0=np.nanmedian(x[y==0])
            s=pd.Series(x[m]); yy=y[m]; q10,q90=s.quantile([.1,.9]); base=yy.mean()
            hi=yy[s.to_numpy()>=q90].mean() if np.any(s.to_numpy()>=q90) else np.nan; lo=yy[s.to_numpy()<=q10].mean() if np.any(s.to_numpy()<=q10) else np.nan
            rows.append({'feature':c,'auc':a,'auc_strength':abs(a-.5)*2,'winner_median':med1,'control_median':med0,'high_decile_lift':hi/base if base else np.nan,'low_decile_lift':lo/base if base else np.nan,'n':int(m.sum())})
    return pd.DataFrame(rows).sort_values('auc_strength',ascending=False)

def thresholds_from_dev(samples,uni,topn=60):
    d=samples[(samples.year<=2018)&samples.sample_type.isin(['POS','CTRL'])].copy(); base=d.target.mean(); rules=[]
    for c in uni.head(topn).feature:
        x=pd.to_numeric(d[c],errors='coerce'); m=x.notna()
        if m.sum()<200: continue
        for q in (.1,.2,.3,.7,.8,.9):
            thr=float(x[m].quantile(q)); side='<=' if q<.5 else '>='; sel=(x<=thr) if q<.5 else (x>=thr); n=int(sel.sum())
            if n<100: continue
            rate=float(d.loc[sel,'target'].mean()); rules.append({'feature':c,'side':side,'threshold':thr,'dev_n':n,'dev_rate':rate,'dev_lift':rate/base})
    return pd.DataFrame(rules).sort_values('dev_lift',ascending=False)

def apply_rule(d,r):
    x=pd.to_numeric(d[r.feature],errors='coerce'); return (x<=r.threshold) if r.side=='<=' else (x>=r.threshold)

def validate_rules(samples,rules):
    periods={'DEV_2010_2018':range(2010,2019),'VAL_2019_2022':range(2019,2023),'OOS_2023_2024':range(2023,2025),'HOLDOUT_2025_2026':range(2025,2027),'NON2020':[y for y in range(2010,2027) if y!=2020]}
    out=[]
    for r in rules.head(120).itertuples():
        rec={'feature':r.feature,'side':r.side,'threshold':r.threshold,'dev_lift':r.dev_lift}; ok=True
        for name,yrs in periods.items():
            d=samples[samples.year.isin(list(yrs))&samples.sample_type.isin(['POS','CTRL'])]; base=d.target.mean(); sel=apply_rule(d,r); n=int(sel.sum()); rate=float(d.loc[sel,'target'].mean()) if n else np.nan; lift=rate/base if base else np.nan
            rec[f'{name}_n']=n; rec[f'{name}_lift']=lift
            if name in ('VAL_2019_2022','OOS_2023_2024','HOLDOUT_2025_2026') and (not np.isfinite(lift) or lift<1.05): ok=False
        rec['stable']=ok; out.append(rec)
    return pd.DataFrame(out).sort_values(['stable','HOLDOUT_2025_2026_lift','OOS_2023_2024_lift'],ascending=False)

def combos(samples,validated):
    stable=validated[validated.stable].head(25); dev=samples[(samples.year<=2018)&samples.sample_type.isin(['POS','CTRL'])]
    periods={'VAL':range(2019,2023),'OOS':range(2023,2025),'HOLD':range(2025,2027)}; rows=[]; rules=list(stable.itertuples())
    for a in range(len(rules)):
        for b in range(a+1,len(rules)):
            ra,rb=rules[a],rules[b]; sd=apply_rule(dev,ra)&apply_rule(dev,rb); n=int(sd.sum())
            if n<100: continue
            rec={'rule1':f'{ra.feature} {ra.side} {ra.threshold:.6g}','rule2':f'{rb.feature} {rb.side} {rb.threshold:.6g}','dev_n':n,'dev_lift':float(dev.loc[sd,'target'].mean()/dev.target.mean())}; stable_all=True
            for name,yrs in periods.items():
                d=samples[samples.year.isin(list(yrs))&samples.sample_type.isin(['POS','CTRL'])]; s=apply_rule(d,ra)&apply_rule(d,rb); nn=int(s.sum()); lift=float(d.loc[s,'target'].mean()/d.target.mean()) if nn and d.target.mean() else np.nan
                rec[f'{name}_n']=nn; rec[f'{name}_lift']=lift
                if not np.isfinite(lift) or lift<1.10 or nn<50: stable_all=False
            rec['stable']=stable_all; rows.append(rec)
    return pd.DataFrame(rows).sort_values(['stable','HOLD_lift','OOS_lift'],ascending=False) if rows else pd.DataFrame()

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    pre=read_pre(); post=read_post(); allp=pd.concat([pre,post],ignore_index=True,sort=False)
    allp=allp.dropna(subset=['date','ticker','close']); allp=allp[(allp.close>0)&(allp.high>0)&(allp.low>0)]
    allp=allp.sort_values(['ticker','date']).drop_duplicates(['ticker','date'],keep='last').reset_index(drop=True)
    master=pd.read_csv(INV/'TCT_GT20_MASTER_EPISODES_2010_2026.csv',parse_dates=['j0']); posmaster=master[master.mfe20_pct>POS_THRESH].copy()
    samples=build_samples(allp,posmaster).replace([np.inf,-np.inf],np.nan); samples.to_parquet(OUT/'TCT_PATTERN_SAMPLES.parquet',index=False)
    uni=univariate(samples,range(2010,2019)); uni.to_csv(OUT/'TCT_UNIVARIATE_DEV.csv',index=False)
    rules=thresholds_from_dev(samples,uni,60); rules.to_csv(OUT/'TCT_CANDIDATE_RULES_DEV.csv',index=False)
    val=validate_rules(samples,rules); val.to_csv(OUT/'TCT_VALIDATED_SINGLE_RULES.csv',index=False)
    cmb=combos(samples,val); cmb.to_csv(OUT/'TCT_VALIDATED_PAIR_PATTERNS.csv',index=False)
    p=samples[samples.target==1].copy(); p.groupby('year').agg(positives=('target','size'),explosive_h4=('winner_h4','sum'),fast_h10=('winner_h10','sum')).reset_index().to_csv(OUT/'TCT_GT25_SAMPLE_YEARLY.csv',index=False)
    feat=[c for c in samples.columns if c.endswith(tuple(f'_L{x}' for x in LAGS))]
    summary={'status':'SUCCESS','rows':len(samples),'positive_rows':int((samples.target==1).sum()),'broad_controls':int((samples.sample_type=='CTRL').sum()),'hard_controls':int((samples.sample_type=='HARD').sum()),'feature_columns':len(feat),'base_feature_families':len(feat)//len(LAGS),'lags':list(LAGS),'dev_years':'2010-2018','validation_years':'2019-2022','oos_years':'2023-2024','holdout_years':'2025-2026','stable_single_rules':int(val.stable.sum()) if not val.empty else 0,'stable_pair_patterns':int(cmb.stable.sum()) if not cmb.empty else 0,'governance':'DISCOVERY_ONLY; historical universe/PEA membership not yet certified survivorship-safe'}
    (OUT/'TCT_PATTERN_SUMMARY.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2)); print('\nTOP SINGLE\n',val.head(20).to_string(index=False)); print('\nTOP PAIRS\n',cmb.head(20).to_string(index=False) if not cmb.empty else 'none')

if __name__=='__main__': main()
