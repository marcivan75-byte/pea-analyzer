from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

HOLDOUT_START=pd.Timestamp('2023-01-01'); EMBARGO=pd.Timedelta(weeks=26); BIG_WIN=.15
MAX_PER_MONTH=5; MAX_PER_YEAR=40; MIN_BIG_RECALL=.90; MIN_EXP_RATIO=.90; MIN_PF_RATIO=.90

def num(df,c):
    if c not in df.columns: raise SystemExit(f'BLOCK_PASS4_DATA: missing {c}')
    return pd.to_numeric(df[c],errors='coerce')

def build(df):
    x=pd.DataFrame(index=df.index)
    x['date']=pd.to_datetime(df['as_of_date'],errors='coerce'); x['ticker']=df['ticker'].astype(str); x['isin']=df['isin'].astype(str)
    x['ret26']=num(df,'forward_ret_true_26w'); x['stop']=df['hit_stop'].astype('boolean'); x['gov']=num(df,'governed_score')
    mom=num(df,'mom_26w'); dd=num(df,'drawdown_4w'); atr=num(df,'atr_14_pct'); close=num(df,'close'); sma200=num(df,'sma200')
    x['momdd']=mom*(1-dd.abs()); x['vold']=atr*dd.abs(); x['trend']=close/sma200-1.0; x['atr']=atr
    good=x['date'].notna()&x['ret26'].notna()&x['stop'].notna()
    for c in ['gov','momdd','vold','trend','atr']: good &= np.isfinite(x[c])
    x=x.loc[good].copy(); x['stop']=x['stop'].astype(bool)
    return x.sort_values(['date','ticker','isin'],kind='stable')

def met(g):
    if g.empty:return {'n':0,'stop_rate':None,'expectancy':None,'profit_factor':None,'win_rate':None,'big_winners':0}
    r=g.ret26.astype(float); w=r[r>0]; l=r[r<=0]; gl=float((-l).sum())
    return {'n':int(len(g)),'stop_rate':float(g.stop.mean()),'expectancy':float(r.mean()),'profit_factor':float(w.sum()/gl) if gl>0 else None,'win_rate':float((r>0).mean()),'big_winners':int((r>=BIG_WIN).sum())}

def cap(g,col):
    z=g.copy(); z['month']=z.date.dt.to_period('M'); z['year']=z.date.dt.year
    z=z.sort_values(['month',col,'date','ticker'],ascending=[True,False,True,True],kind='stable').groupby('month',group_keys=False).head(MAX_PER_MONTH)
    z=z.sort_values(['year',col,'date','ticker'],ascending=[True,False,True,True],kind='stable').groupby('year',group_keys=False).head(MAX_PER_YEAR)
    return z.sort_values(['date',col,'ticker'],ascending=[True,False,True],kind='stable')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input-dir',type=Path,required=True); ap.add_argument('--pass3-report',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    p3=json.loads(a.pass3_report.read_text())
    if p3.get('governance',{}).get('holdout_accessed') is not False or p3.get('selected',{}).get('variant')!='R_STOP_08': raise SystemExit('BLOCK_PASS4_GOVERNANCE: pass3 not frozen')
    x=build(pd.read_csv(a.input_dir/'V22_1_TRAIN.csv',low_memory=False)); cutoff=HOLDOUT_START-EMBARGO
    if x.empty or x.date.max()>=cutoff: raise SystemExit('BLOCK_PASS4_EMBARGO')
    valid=x.iloc[int(len(x)*.80):].copy(); risk=(-valid.momdd).rank(pct=True); valid=valid.loc[risk<=.90].copy()
    valid['pgov']=valid.gov.rank(pct=True); valid['pvold_good']=1-valid.vold.rank(pct=True)
    valid['ptrend']=valid.trend.rank(pct=True); valid['patr']=valid.atr.rank(pct=True)
    valid['STATIC']=.92*valid.pgov+.08*valid.pvold_good
    # PIT regime-adaptive penalties. Regime boundaries are pre-2023 empirical terciles only.
    tq1,tq2=valid.trend.quantile([1/3,2/3]); aq1,aq2=valid.atr.quantile([1/3,2/3])
    wt=np.where(valid.trend<=tq1,.12,np.where(valid.trend>=tq2,.04,.08))
    wv=np.where(valid.atr>=aq2,.12,np.where(valid.atr<=aq1,.04,.08))
    wc=np.clip((wt+wv)/2,.04,.12)
    valid['TREND_ADAPT']=(1-wt)*valid.pgov+wt*valid.pvold_good
    valid['VOL_ADAPT']=(1-wv)*valid.pgov+wv*valid.pvold_good
    valid['COMBINED_ADAPT']=(1-wc)*valid.pgov+wc*valid.pvold_good
    variants=['STATIC','TREND_ADAPT','VOL_ADAPT','COMBINED_ADAPT']; sels={v:cap(valid,v) for v in variants}; base=sels['STATIC']; bm=met(base); basebig=max(bm['big_winners'],1)
    rows=[]; best=None
    for v in variants:
        s=sels[v]; m=met(s); br=m['big_winners']/basebig; admiss=br>=MIN_BIG_RECALL and m['expectancy']>=bm['expectancy']*MIN_EXP_RATIO and m['profit_factor']>=bm['profit_factor']*MIN_PF_RATIO
        row={'variant':v,'admissible':bool(admiss),'big_winner_recall_vs_static':float(br),**m}; rows.append(row)
        key=(-float(m['stop_rate']),float(m['expectancy']),float(m['profit_factor']),float(br))
        if admiss and (best is None or key>best[0]): best=(key,v,row)
    if best is None: raise SystemExit('BLOCK_PASS4_MODEL: no adaptive regime variant satisfies guards')
    if best[1]=='STATIC' or best[2]['stop_rate']>=bm['stop_rate']: raise SystemExit('BLOCK_PASS4_MODEL: no adaptive stop improvement')
    out=a.out_dir; out.mkdir(parents=True,exist_ok=True); pd.DataFrame(rows).to_csv(out/'PASS4_VARIANTS.csv',index=False); sels[best[1]].to_csv(out/'PASS4_SELECTED_PRE2023.csv',index=False)
    report={'version':'V22.1_TABPORT_PASS4_REGIME_ADAPTIVE_1','governance':{'holdout_accessed':False,'holdout_scope':'SEALED_UNTIL_FINAL_PASS6_EVALUATION','training_source':'PRE_2023_PIT_ONLY','embargo_weeks':26,'train_max_date':str(x.date.max().date()),'regime_inputs':['close/sma200-1','atr_14_pct'],'regime_threshold_source':'PRE2023_VALIDATION_TERCILES','capacity':{'max_entries_month':5,'max_entries_year':40}},'regime_thresholds':{'trend_q33':float(tq1),'trend_q67':float(tq2),'atr_q33':float(aq1),'atr_q67':float(aq2)},'static':bm,'selected':{'variant':best[1],'metrics':best[2]},'variants':rows,'promotion_automatic':False}
    (out/'PASS4_REPORT.json').write_text(json.dumps(report,indent=2,sort_keys=True)); print(json.dumps(report,indent=2,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
