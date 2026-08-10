from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd
@dataclass(frozen=True)
class TradeConfig:
    horizon_sessions:int=20; target_return_pct:float=15.; stop_loss_pct:float=10.; same_bar_policy:str='STOP_FIRST'
def normalise_ohlc(frame):
    rename={}
    for c in frame.columns:
        x=str(c).strip().lower()
        if x in {'date','timestamp','datetime','session_date'}:rename[c]='date'
        elif x in {'instrument','instrument_id','isin','ticker','symbol'}:rename[c]='instrument_id'
        elif x in {'open','high','low','close'}:rename[c]=x
    out=frame.rename(columns=rename).copy(); req={'date','instrument_id','open','high','low','close'}; miss=req-set(out.columns)
    if miss:raise ValueError(f'Missing OHLC columns: {sorted(miss)}')
    out['date']=pd.to_datetime(out.date,errors='coerce')
    for c in ['open','high','low','close']:out[c]=pd.to_numeric(out[c],errors='coerce')
    out=out.dropna(subset=list(req)); return out[(out.open>0)&(out.high>0)&(out.low>0)&(out.close>0)].sort_values(['instrument_id','date']).reset_index(drop=True)
def make_trade_outcomes(ohlc,cfg=None):
    cfg=cfg or TradeConfig(); data=normalise_ohlc(ohlc); rows=[]
    for inst,g in data.groupby('instrument_id',sort=False):
        g=g.reset_index(drop=True)
        for si in range(len(g)-1):
            ei=si+1; end=min(len(g)-1,ei+cfg.horizon_sessions-1); entry=float(g.loc[ei,'open']); target=entry*(1+cfg.target_return_pct/100); stop=entry*(1-cfg.stop_loss_pct/100); w=g.loc[ei:end]; mfe=float((w.high/entry-1).max()*100); mae=float((w.low/entry-1).min()*100); ret=None; reason='TIME'; sessions=len(w); hit=False
            for j,(_,bar) in enumerate(w.iterrows(),1):
                ht=float(bar.high)>=target; hs=float(bar.low)<=stop
                if ht and hs:
                    if cfg.same_bar_policy.upper()=='STOP_FIRST':ret=-cfg.stop_loss_pct; reason='STOP_SAME_BAR'
                    else:ret=cfg.target_return_pct; reason='TARGET_SAME_BAR'; hit=True
                    sessions=j; break
                if hs:ret=-cfg.stop_loss_pct; reason='STOP'; sessions=j; break
                if ht:ret=cfg.target_return_pct; reason='TARGET'; sessions=j; hit=True; break
            if ret is None:ret=float((w.iloc[-1].close/entry-1)*100)
            rows.append({'snapshot_date':g.loc[si,'date'],'instrument_id':str(inst),'entry_date':g.loc[ei,'date'],'entry_price_next_open':entry,'trade_return_pct':float(ret),'exit_reason':reason,'sessions_to_exit':int(sessions),'target_before_stop':bool(hit),'mfe_pct':mfe,'mae_pct':mae})
    return pd.DataFrame(rows)
def precision_at_k(df,k,label='target_before_stop'):
    vals=[]
    for _,g in df.groupby('snapshot_date'):
        c=g.nlargest(min(k,len(g)),'score')
        if len(c):vals.append(float(c[label].astype(bool).mean()))
    return float(np.mean(vals)) if vals else math.nan
def ece(p,y,bins=10):
    d=pd.DataFrame({'p':pd.to_numeric(p,errors='coerce'),'y':y.astype(float)}).dropna()
    if d.empty:return math.nan
    d.p=d.p.where(d.p<=1,d.p/100).clip(0,1); d['b']=pd.cut(d.p,np.linspace(0,1,bins+1),include_lowest=True); total=len(d); value=0.
    for _,g in d.groupby('b',observed=True):
        if len(g):value+=len(g)/total*abs(float(g.p.mean())-float(g.y.mean()))
    return float(value)
def evaluate_scores(scored,threshold=70,k=20,probability_col=None):
    d=scored.copy(); d['score']=pd.to_numeric(d.score,errors='coerce'); d=d.dropna(subset=['score','target_before_stop','trade_return_pct']); y=d.target_before_stop.astype(bool); sel=d.score>=threshold; tp=int((sel&y).sum()); fp=int((sel&~y).sum()); fn=int((~sel&y).sum()); precision=tp/(tp+fp) if tp+fp else math.nan; recall=tp/(tp+fn) if tp+fn else math.nan; base=float(y.mean()) if len(y) else math.nan; r=pd.to_numeric(d.loc[sel,'trade_return_pct'],errors='coerce').dropna(); wins=r[r>0]; losses=r[r<0]; gp=float(wins.sum()); gl=float(abs(losses.sum())); pf=gp/gl if gl>0 else (math.inf if gp>0 else math.nan); aw=float(wins.mean()) if len(wins) else math.nan; al=float(losses.mean()) if len(losses) else math.nan; payoff=aw/abs(al) if pd.notna(aw) and pd.notna(al) and al<0 else math.nan; brier=calerr=math.nan
    if probability_col and probability_col in d:
        p=pd.to_numeric(d[probability_col],errors='coerce'); m=p.notna(); p01=p.where(p<=1,p/100).clip(0,1)
        if m.any():brier=float(((p01[m]-y[m].astype(float))**2).mean()); calerr=ece(p[m],y[m])
    return {'observations':float(len(d)),'positives':float(y.sum()),'selected':float(sel.sum()),'precision':float(precision) if pd.notna(precision) else math.nan,'recall':float(recall) if pd.notna(recall) else math.nan,'base_rate':base,'lift_vs_base':float(precision/base) if pd.notna(precision) and base>0 else math.nan,'precision_at_k':precision_at_k(d,k),'win_rate':float((r>0).mean()) if len(r) else math.nan,'expectancy_pct':float(r.mean()) if len(r) else math.nan,'avg_win_pct':aw,'avg_loss_pct':al,'payoff_ratio':float(payoff) if pd.notna(payoff) else math.nan,'profit_factor':float(pf) if pd.notna(pf) else math.nan,'mean_mfe_pct':float(pd.to_numeric(d.loc[sel,'mfe_pct'],errors='coerce').mean()) if sel.any() else math.nan,'mean_mae_pct':float(pd.to_numeric(d.loc[sel,'mae_pct'],errors='coerce').mean()) if sel.any() else math.nan,'brier':brier,'ece':calerr}
def _pava(v,w):
    blocks=[{'v':float(a),'w':float(b),'n':1} for a,b in zip(v,w)]; i=0
    while i<len(blocks)-1:
        if blocks[i]['v']<=blocks[i+1]['v']+1e-15:i+=1;continue
        a,b=blocks[i],blocks[i+1]; ww=a['w']+b['w']; blocks[i:i+2]=[{'v':(a['v']*a['w']+b['v']*b['w'])/ww,'w':ww,'n':a['n']+b['n']}]; i=max(0,i-1)
    out=[]
    for b in blocks:out += [b['v']]*b['n']
    return np.asarray(out,float)
@dataclass
class MonotonicBinCalibrator:
    edges:np.ndarray; probabilities:np.ndarray
    @classmethod
    def fit(cls,score,target,bins=10):
        x=pd.to_numeric(score,errors='coerce'); y=target.astype(float); m=x.notna()&y.notna(); x=x[m]; y=y[m]
        if len(x)<max(30,bins*3):raise ValueError('Insufficient observations for calibration')
        q=min(bins,max(2,int(x.nunique()))); b=pd.qcut(x,q=q,duplicates='drop'); s=pd.DataFrame({'x':x,'y':y,'b':b}).groupby('b',observed=True).agg(high=('x','max'),rate=('y','mean'),n=('y','size')).reset_index(drop=True); return cls(s.high.to_numpy(float),_pava(s.rate.to_numpy(float),s.n.to_numpy(float)))
    def predict(self,score):
        x=pd.to_numeric(score,errors='coerce'); idx=np.searchsorted(self.edges,x.fillna(self.edges[0]).to_numpy(float),side='left'); idx=np.clip(idx,0,len(self.probabilities)-1); return pd.Series(self.probabilities[idx],index=x.index).where(x.notna())
def purged_holdout(scored,threshold=70,test_fraction=.30,purge_sessions=20,bins=10,min_positive=100,min_train_snapshots=120,k=20):
    d=scored.copy(); d['snapshot_date']=pd.to_datetime(d.snapshot_date,errors='coerce'); d['score']=pd.to_numeric(d.score,errors='coerce'); d=d.dropna(subset=['snapshot_date','score','target_before_stop']); dates=sorted(d.snapshot_date.unique())
    if len(dates)<min_train_snapshots+max(10,purge_sessions):return {'status':'INSUFFICIENT_HISTORY','snapshot_count':len(dates),'probability_calibrated':False}
    split=int(len(dates)*(1-test_fraction)); split=min(max(split,min_train_snapshots+purge_sessions),len(dates)-1); train_dates=dates[:max(0,split-purge_sessions)]; purged=dates[max(0,split-purge_sessions):split]; test_dates=dates[split:]; train=d[d.snapshot_date.isin(train_dates)].copy(); test=d[d.snapshot_date.isin(test_dates)].copy(); positives=int(train.target_before_stop.astype(bool).sum()); result={'train_snapshot_count':len(train_dates),'purged_snapshot_count':len(purged),'test_snapshot_count':len(test_dates),'train_positive_events':positives,'purge_sessions':purge_sessions,'probability_calibrated':False}
    if positives<min_positive or len(train_dates)<min_train_snapshots:result['status']='INSUFFICIENT_FOR_CALIBRATION';result['metrics']=evaluate_scores(test,threshold,k);return result
    cal=MonotonicBinCalibrator.fit(train.score,train.target_before_stop,bins); test['probability']=cal.predict(test.score); result.update({'status':'PURGED_HOLDOUT_CALIBRATED','probability_calibrated':True,'metrics':evaluate_scores(test,threshold,k,'probability')}); return result
def purged_walk_forward(scored,threshold=70,min_train_snapshots=120,test_snapshots=20,step_snapshots=20,purge_sessions=20,bins=10,min_positive=100,k=20):
    d=scored.copy(); d['snapshot_date']=pd.to_datetime(d.snapshot_date,errors='coerce'); d=d.dropna(subset=['snapshot_date','score','target_before_stop']); dates=sorted(d.snapshot_date.unique()); folds=[]; start=min_train_snapshots+purge_sessions
    while start<len(dates):
        train_dates=dates[:max(0,start-purge_sessions)]; test_dates=dates[start:min(start+test_snapshots,len(dates))]
        if not test_dates:break
        train=d[d.snapshot_date.isin(train_dates)].copy(); test=d[d.snapshot_date.isin(test_dates)].copy(); positives=int(train.target_before_stop.astype(bool).sum()); fold={'train_snapshots':len(train_dates),'test_snapshots':len(test_dates),'train_positive_events':positives}
        if positives>=min_positive and len(train_dates)>=min_train_snapshots:cal=MonotonicBinCalibrator.fit(train.score,train.target_before_stop,bins);test['probability']=cal.predict(test.score);fold['calibrated']=True;fold['metrics']=evaluate_scores(test,threshold,k,'probability')
        else:fold['calibrated']=False;fold['metrics']=evaluate_scores(test,threshold,k)
        folds.append(fold);start+=step_snapshots
    return {'status':'OK' if folds else 'INSUFFICIENT_HISTORY','fold_count':len(folds),'calibrated_fold_count':sum(bool(f['calibrated']) for f in folds),'purge_sessions':purge_sessions,'folds':folds}
