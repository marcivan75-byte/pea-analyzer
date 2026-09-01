"""Calibration walk-forward des probabilités de stop et de succès Meta.

Ce module est volontairement technique-only: les historiques PIT préopen/secteur ne sont
pas disponibles et ne sont jamais synthétisés. Chaque vintage trimestriel n'utilise que
des outcomes 126 séances déjà entièrement maturés avant le début du trimestre.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss

from v182.hebdo.false_positive_filter import FalsePositiveFilter
from v182.hebdo.mae_predictor import MAEPredictor
from v182.hebdo.tabport_publish import _indicators_one, PARIS

FEATURES=['vol_z','drawdown_4w','atr_14_pct','close_vs_sma200','log_adv_20m','rsi_14_hebdo','risk_stop_9_proxy']
EMBARGO_DAYS=182


def _weekly_rsi(technical:pd.DataFrame)->pd.DataFrame:
    rows=[]
    for ticker,g in technical.groupby('ticker',sort=False):
        x=g.sort_values('date').copy()
        x['week']=x['date'].dt.tz_localize(None).dt.to_period('W-FRI').astype(str)
        w=x.groupby('week',as_index=False).tail(1)[['week','close']].copy()
        delta=pd.to_numeric(w['close']).diff(); gain=delta.clip(lower=0); loss=(-delta.clip(upper=0))
        ag=gain.rolling(14,min_periods=14).mean(); al=loss.rolling(14,min_periods=14).mean(); rs=ag/al.replace(0,np.nan)
        rsi=100-(100/(1+rs)); rsi[(al==0)&(ag>0)]=100.0; rsi[(al==0)&(ag==0)]=50.0
        w['rsi_14_hebdo']=rsi; w['ticker']=ticker; rows.append(w[['week','ticker','rsi_14_hebdo']])
    return pd.concat(rows,ignore_index=True)


def build_walkforward_candidates(ohlcv:pd.DataFrame)->pd.DataFrame:
    technical=pd.concat([_indicators_one(g) for _,g in ohlcv.groupby('ticker',sort=False)],ignore_index=True)
    technical=technical.sort_values(['date','ticker']).reset_index(drop=True)
    technical['week']=technical['date'].dt.tz_localize(None).dt.to_period('W-FRI').astype(str)
    wrsi=_weekly_rsi(technical)
    technical=technical.merge(wrsi,on=['week','ticker'],how='left',validate='many_to_one')
    b=technical.loc[technical['B_signal'],['week','ticker','B_signal_type']].sort_values(['week','ticker']).drop_duplicates(['week','ticker'],keep='last')
    week_end=technical.sort_values('date').groupby(['week','ticker'],as_index=False).tail(1)
    c=week_end.merge(b,on=['week','ticker'],how='inner',suffixes=('','_trigger'))
    market_date=pd.to_datetime(c['date'],utc=True)
    c['market_snapshot_date']=market_date
    c['date']=market_date+pd.Timedelta(days=1)
    snaps=[]
    for ts in market_date:
        d=ts.tz_convert(PARIS).date(); snaps.append(pd.Timestamp(f'{d} 21:59:00',tz=PARIS).tz_convert('UTC'))
    c['pit_snapshot_time']=snaps
    c['mom_26w_sector']=0.0
    c['sector_momentum_status']='UNAVAILABLE_NOT_MODEL_FEATURE'
    c['signal_family']=c.get('B_signal_type_trigger',c.get('B_signal_type','B'))
    need=['close','sma200','vol_z','drawdown_4w','atr_14_pct','adv_20m_eur','rsi_14_hebdo']
    c=c.dropna(subset=need).copy()
    # Etage 1 anti-FP déjà déterministe et disponible à la date du signal.
    c=FalsePositiveFilter().filter_batch(c)
    if c.empty: raise ValueError('BLOCK_WALKFORWARD: no candidates after deterministic FP filter')
    # Proxy historique stable comme feature; il n'est PAS labellisé calibré.
    c=MAEPredictor().predict_batch(c)
    c['close_vs_sma200']=(pd.to_numeric(c['close'])<pd.to_numeric(c['sma200'])).astype(float)
    c['log_adv_20m']=np.log1p(pd.to_numeric(c['adv_20m_eur']).clip(lower=0))
    c=c.replace([np.inf,-np.inf],np.nan).dropna(subset=FEATURES).copy()
    c=c.sort_values(['date','ticker']).reset_index(drop=True)
    c['candidate_id']=np.arange(len(c),dtype=int)
    return c


def attach_mature_outcomes(candidates:pd.DataFrame,ohlcv:pd.DataFrame,horizon:int=126,stop_pct:float=0.09)->pd.DataFrame:
    price_groups={str(t):g.sort_values('date').reset_index(drop=True) for t,g in ohlcv.groupby('ticker',sort=False)}
    rows=[]
    for _,r in candidates.iterrows():
        g=price_groups.get(str(r['ticker']))
        if g is None: continue
        decision=pd.to_datetime(r['date'],utc=True)
        fut=g[g['date']>decision].head(horizon)
        if len(fut)<horizon: continue
        entry=float(fut.iloc[0]['open'])
        if not np.isfinite(entry) or entry<=0: continue
        lows=pd.to_numeric(fut['low'],errors='coerce'); highs=pd.to_numeric(fut['high'],errors='coerce'); closes=pd.to_numeric(fut['close'],errors='coerce')
        if lows.isna().any() or highs.isna().any() or closes.isna().any(): continue
        mae=float(lows.min()/entry-1); mfe=float(highs.max()/entry-1)
        stop_level=entry*(1-stop_pct); hit_mask=lows<=stop_level; hit_stop=bool(hit_mask.any())
        if hit_stop:
            first=int(np.flatnonzero(hit_mask.to_numpy())[0]); bar=fut.iloc[first]
            op=float(bar['open']); realized=(op/entry-1) if op<stop_level else -stop_pct
            outcome_return=float(realized)
        else:
            outcome_return=float(closes.iloc[-1]/entry-1)
        z=r.to_dict(); z.update({'entry_outcome_price':entry,'mae':mae,'mfe':mfe,'hit_stop':hit_stop,'outcome_return':outcome_return,
                                 'outcome_end_date':pd.to_datetime(fut.iloc[-1]['date'],utc=True),'outcome_sessions':horizon})
        z['meta_label']=int((mfe>0.08) and (mae>-stop_pct) and (not hit_stop))
        rows.append(z)
    out=pd.DataFrame(rows)
    if out.empty: raise ValueError('BLOCK_WALKFORWARD: no matured outcomes')
    return out.sort_values(['date','ticker']).reset_index(drop=True)


@dataclass
class CalibratedClassifier:
    target:str
    base:object=None
    iso:object=None
    status:str='UNTRAINED'
    metrics:dict|None=None

    def fit(self,history:pd.DataFrame)->dict:
        work=history.copy().sort_values('date').reset_index(drop=True)
        if len(work)<90:
            self.status='BLOCK_TOO_FEW_ROWS'; return {'status':self.status,'n':len(work)}
        times=pd.to_datetime(work['date'],utc=True); start=times.min(); end=times.max(); embargo=pd.Timedelta(days=EMBARGO_DAYS)
        effective=(end-start)-2*embargo
        if effective<=pd.Timedelta(0):
            self.status='BLOCK_TOO_SHORT_TEMPORAL_SPAN'; return {'status':self.status,'n':len(work)}
        train_end=start+effective*0.60; cal_start=train_end+embargo; cal_end=cal_start+effective*0.20; test_start=cal_end+embargo
        train=work[times<=train_end]; cal=work[(times>=cal_start)&(times<=cal_end)]; test=work[times>=test_start]
        if min(len(train),len(cal),len(test))<20:
            self.status='BLOCK_SPLIT_TOO_SMALL'; return {'status':self.status,'n':len(work),'n_train':len(train),'n_cal':len(cal),'n_test':len(test)}
        for part in [train,cal,test]:
            counts=part[self.target].value_counts()
            if set(counts.index)!={0,1} or counts.min()<10:
                self.status='BLOCK_CLASS_SUPPORT'; return {'status':self.status,'n':len(work)}
        base=RandomForestClassifier(n_estimators=120,max_depth=6,min_samples_leaf=20,random_state=42,class_weight='balanced',n_jobs=-1)
        base.fit(train[FEATURES],train[self.target].astype(int))
        raw_cal=base.predict_proba(cal[FEATURES])[:,1]
        if np.unique(raw_cal).size<2:
            self.status='BLOCK_DEGENERATE_CALIBRATION'; return {'status':self.status,'n':len(work)}
        iso=IsotonicRegression(out_of_bounds='clip').fit(raw_cal,cal[self.target].astype(int))
        prob=np.asarray(iso.transform(base.predict_proba(test[FEATURES])[:,1]),dtype=float)
        y=test[self.target].astype(int).to_numpy(); brier=float(brier_score_loss(y,prob))
        prevalence=float(train[self.target].mean()); naive=float(np.mean((y-prevalence)**2))
        if not np.isfinite(brier) or brier>naive:
            self.status='BLOCK_NO_OOS_BRIER_EDGE'; return {'status':self.status,'n':len(work),'brier':brier,'naive_brier':naive,'n_test':len(test)}
        self.base=base; self.iso=iso; self.status='CALIBRATED_PURGED_TEMPORAL_OOS'
        self.metrics={'status':self.status,'n':len(work),'n_train':len(train),'n_cal':len(cal),'n_test':len(test),'brier':brier,'naive_brier':naive,
                      'train_end':str(train_end),'cal_start':str(cal_start),'cal_end':str(cal_end),'test_start':str(test_start),'embargo_days':EMBARGO_DAYS}
        return self.metrics

    def predict(self,df:pd.DataFrame)->np.ndarray:
        if self.base is None or self.iso is None: raise ValueError(f'BLOCK_WALKFORWARD_MODEL: {self.target} not calibrated')
        return np.asarray(self.iso.transform(self.base.predict_proba(df[FEATURES])[:,1]),dtype=float)


def _quarter_start(ts:pd.Timestamp)->pd.Timestamp:
    ts=pd.Timestamp(ts)
    if ts.tzinfo is None: ts=ts.tz_localize('UTC')
    else: ts=ts.tz_convert('UTC')
    month=((ts.month-1)//3)*3+1
    return pd.Timestamp(year=ts.year,month=month,day=1,tz='UTC')


def walkforward_score(outcomes:pd.DataFrame,fee:float=0.003)->tuple[pd.DataFrame,pd.DataFrame]:
    work=outcomes.copy(); work['date']=pd.to_datetime(work['date'],utc=True); work['outcome_end_date']=pd.to_datetime(work['outcome_end_date'],utc=True)
    work['quarter_start']=work['date'].map(_quarter_start)
    scored=[]; vintages=[]
    for q,score_rows in work.groupby('quarter_start',sort=True):
        hist=work[work['outcome_end_date']<q].copy()
        stop=CalibratedClassifier('hit_stop'); meta=CalibratedClassifier('meta_label')
        sm=stop.fit(hist); mm=meta.fit(hist)
        vintage={'quarter_start':str(q),'history_rows':int(len(hist)),'stop_model':sm,'meta_model':mm,'status':'BLOCKED'}
        if stop.status!='CALIBRATED_PURGED_TEMPORAL_OOS' or meta.status!='CALIBRATED_PURGED_TEMPORAL_OOS':
            vintages.append(vintage); continue
        train_returns=pd.to_numeric(hist['outcome_return'],errors='coerce').dropna()
        wins=train_returns[train_returns>0]; losses=train_returns[train_returns<=0]
        if len(wins)<20 or len(losses)<20:
            vintage['status']='BLOCK_PAYOFF_SUPPORT'; vintages.append(vintage); continue
        avg_win=float(wins.mean()); avg_loss=float(losses.mean())
        if avg_win<=0 or avg_loss>=0:
            vintage['status']='BLOCK_PAYOFF_SIGN'; vintages.append(vintage); continue
        s=score_rows.copy(); s['prob_stop_9']=stop.predict(s); s['prob_meta']=meta.predict(s)
        pwin=np.clip(s['prob_meta'].to_numpy(float),0,1); ploss=np.clip(s['prob_stop_9'].to_numpy(float),0,1); total=pwin+ploss
        over=total>0.95; pwin[over]=pwin[over]/total[over]*0.95; ploss[over]=ploss[over]/total[over]*0.95
        pflat=np.maximum(0,1-pwin-ploss); flat=float(train_returns[(train_returns>-0.02)&(train_returns<0.02)].mean()) if ((train_returns>-0.02)&(train_returns<0.02)).any() else 0.0
        s['EV_net']=pwin*avg_win+ploss*avg_loss+pflat*flat-fee
        s['mae_model_status']='CALIBRATED_PURGED_TEMPORAL_OOS'; s['meta_model_status']='CALIBRATED_PURGED_TEMPORAL_OOS'; s['ev_model_status']='EMPIRICAL_MATURED_WALK_FORWARD'
        s['model_vintage']=str(q); s['selection_confidence']='CALIBRATED_TECHNICAL_ONLY'
        # Classement strictement cross-sectionnel à chaque date, puis EV >= 0.
        chunks=[]
        for d,g in s.groupby('date',sort=True):
            h=g.copy(); q85=float(h['EV_net'].quantile(.85)); q60=float(h['EV_net'].quantile(.60)); h['tier']='EXCLU'
            h.loc[h['EV_net']>=max(q85,0.0),'tier']='TCT'; h.loc[(h['EV_net']>=max(q60,0.0))&(h['EV_net']<max(q85,0.0)),'tier']='CT_WATCH'; h.loc[h['EV_net']<0,'tier']='EXCLU'; chunks.append(h)
        s=pd.concat(chunks,ignore_index=True); s=s[s['tier'].isin(['TCT','CT_WATCH'])].copy()
        vintage.update({'status':'VALIDATED','avg_win':avg_win,'avg_loss':avg_loss,'scored_rows':int(len(score_rows)),'eligible_signals':int(len(s))})
        vintages.append(vintage); scored.append(s)
    return (pd.concat(scored,ignore_index=True).sort_values(['date','EV_net','ticker'],ascending=[True,False,True]).reset_index(drop=True) if scored else pd.DataFrame(), pd.DataFrame(vintages))
