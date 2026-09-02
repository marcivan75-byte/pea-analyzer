"""Research-only PIT walk-forward META training for TABPORT.

Fixes the demonstrated UNTRAINED prob_meta=0.5 wiring defect without changing
production. Development is expanding walk-forward through 2022. For all
2023-2026 decisions the training set is frozen before 2023 and never includes
holdout outcomes.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

from v182.backtests.v21_8_1_backtest_B_v2 import compute_true_26w_pnl
from v182.hebdo.expected_value_ranker import ExpectedValueRanker
from v182.hebdo.false_positive_filter import FalsePositiveFilter
from v182.hebdo.mae_predictor import MAEPredictor
from v182.hebdo.meta_labeler import MetaLabeler
from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_longitudinal_audit73 import load_governed_ohlcv
from v182.hebdo.tabport_publish import _indicators_one, build_weekly_meta_signals

HOLDOUT_START=pd.Timestamp('2023-01-01',tz='UTC')
EMBARGO_DAYS=182


def _weekly_rsi_map(technical: pd.DataFrame, period:int=14) -> pd.DataFrame:
    base=technical[['date','ticker','close']].copy()
    base['week']=base['date'].dt.tz_localize(None).dt.to_period('W-FRI').astype(str)
    weekly=base.sort_values('date').groupby(['ticker','week'],as_index=False).tail(1).sort_values(['ticker','date'])
    parts=[]
    for ticker,g in weekly.groupby('ticker',sort=False):
        x=g.copy(); d=x['close'].astype(float).diff(); gain=d.clip(lower=0); loss=(-d.clip(upper=0))
        ag=gain.ewm(alpha=1/period,adjust=False,min_periods=period).mean(); al=loss.ewm(alpha=1/period,adjust=False,min_periods=period).mean()
        rs=ag/al.replace(0,np.nan); x['rsi_14_hebdo']=100-(100/(1+rs)); x.loc[(al==0)&(ag>0),'rsi_14_hebdo']=100.0
        x['ticker']=str(ticker); parts.append(x)
    if not parts:
        return pd.DataFrame(columns=['ticker','week','rsi_14_hebdo'])
    weekly=pd.concat(parts,ignore_index=True)
    return weekly[['ticker','week','rsi_14_hebdo']]


def build_pre_meta_candidates(ohlcv:pd.DataFrame) -> tuple[pd.DataFrame,dict]:
    technical=pd.concat([_indicators_one(g) for _,g in ohlcv.groupby('ticker',sort=False)],ignore_index=True)
    technical=technical.sort_values(['date','ticker']).reset_index(drop=True)
    technical['week']=technical['date'].dt.tz_localize(None).dt.to_period('W-FRI').astype(str)
    rsi=_weekly_rsi_map(technical)
    b=technical.loc[technical['B_signal'],['week','ticker','B_signal_type']].sort_values(['week','ticker']).drop_duplicates(['week','ticker'],keep='last')
    week_end=technical.sort_values('date').groupby(['week','ticker'],as_index=False).tail(1)
    c=week_end.merge(b,on=['week','ticker'],how='inner',suffixes=('','_trigger')).merge(rsi,on=['week','ticker'],how='left')
    dates=pd.Index(sorted(technical['date'].unique()))
    if len(dates)<=126: raise ValueError('BLOCK_META_WF_INSUFFICIENT_HISTORY')
    mature_cutoff=pd.Timestamp(dates[-127]); c=c[c['date']<=mature_cutoff].copy()
    c['market_snapshot_date']=pd.to_datetime(c['date'],utc=True); c['date']=c['market_snapshot_date']+pd.Timedelta(days=1)
    c['mom_26w_sector']=0.0; c['sector_momentum_status']='UNAVAILABLE_CONSERVATIVE_ZERO'
    c['signal_family']=c.get('B_signal_type_trigger',c.get('B_signal_type','B'))
    need=['close','sma200','vol_z','drawdown_4w','atr_14_pct','adv_20m_eur','rsi_14_hebdo']
    c=c.dropna(subset=need).copy()
    c=FalsePositiveFilter().filter_batch(c)
    c=MAEPredictor().predict_batch(c)
    c=c.sort_values(['date','ticker']).drop_duplicates(['date','ticker']).reset_index(drop=True)
    return c,{'pre_meta_candidates':int(len(c)),'mature_market_cutoff':str(mature_cutoff),'rsi_feature':'WEEKLY_WILDER_EWM_14'}


def add_outcomes(candidates:pd.DataFrame,ohlcv:pd.DataFrame) -> pd.DataFrame:
    by={t:g.sort_values('date').reset_index(drop=True) for t,g in ohlcv.groupby('ticker',sort=False)}
    rows=[]
    for _,r in candidates.iterrows():
        g=by.get(str(r['ticker']))
        if g is None: continue
        d=pd.Timestamp(r['market_snapshot_date']); future=g[g['date']>d].head(126)
        res=compute_true_26w_pnl(float(r['close']),future[['open','high','low','close']],0.09,126)
        z=r.to_dict(); z.update({'mfe':res.get('mfe'),'mae':res.get('mae'),'hit_stop':res.get('hit_stop'),'outcome_block_reason':res.get('block_reason')}); rows.append(z)
    out=pd.DataFrame(rows)
    complete=out[['mfe','mae','hit_stop']].notna().all(axis=1) & out['outcome_block_reason'].isna()
    labeled=MetaLabeler(label_horizon_days=EMBARGO_DAYS).build_meta_label(out.loc[complete].copy())
    return labeled


def training_cutoff_for_year(year:int)->pd.Timestamp:
    score_start=pd.Timestamp(f'{year}-01-01',tz='UTC')
    freeze=min(score_start,HOLDOUT_START)
    return freeze-pd.Timedelta(days=EMBARGO_DAYS)


def walkforward_signals(candidates:pd.DataFrame,labeled:pd.DataFrame) -> tuple[pd.DataFrame,pd.DataFrame]:
    parts=[]; audits=[]
    years=sorted(candidates['date'].dt.year.unique())
    for year in years:
        score=candidates[candidates['date'].dt.year==year].copy()
        cutoff=training_cutoff_for_year(int(year))
        train=labeled[labeled['date']<=cutoff].copy()
        model=MetaLabeler(label_horizon_days=EMBARGO_DAYS)
        train_result={'status':'BLOCK_NO_TRAINING_ROWS','n':0}
        if len(train): train_result=model.train(train)
        trained=train_result.get('status')=='TRAINED_PURGED_TEMPORAL_OOS'
        for decision,grp in score.groupby('date',sort=True):
            if trained:
                s=model.predict_proba(grp)
            else:
                s=grp.copy(); s['prob_meta']=0.5; s['meta_model_status']=train_result.get('status','UNTRAINED')
            ranked=ExpectedValueRanker().rank_batch(s)
            ranked['date']=decision; ranked['wf_train_cutoff']=cutoff; ranked['wf_train_n']=len(train); ranked['wf_trained']=trained
            parts.append(ranked)
        audits.append({'year':int(year),'score_rows':int(len(score)),'training_cutoff':str(cutoff),'training_rows':int(len(train)),'training_status':train_result.get('status'),'holdout_training_frozen':bool(year>=2023)})
    if not parts: raise ValueError('BLOCK_META_WF_NO_SCORED_SIGNALS')
    out=pd.concat(parts,ignore_index=True)
    out=out[out['tier'].isin(['TCT','CT_WATCH']) & (pd.to_numeric(out['EV_net'],errors='coerce')>=0)].copy()
    return out.sort_values(['date','EV_net','ticker'],ascending=[True,False,True]).reset_index(drop=True),pd.DataFrame(audits)


def _segment_run(signals:pd.DataFrame,ohlcv:pd.DataFrame,start:pd.Timestamp|None,end:pd.Timestamp|None) -> dict:
    s=signals.copy()
    if start is not None: s=s[s['date']>=start]
    if end is not None: s=s[s['date']<end]
    if s.empty: return {'status':'EMPTY'}
    needed=set(s['ticker'].astype(str)); prices=ohlcv[ohlcv['ticker'].astype(str).isin(needed)][['date','ticker','open','high','low','close']].copy()
    r=Tabport65k(TabportConfig()).run(s,prices); m=r['metrics'].copy(); m['status']='OK'; return m


def run(pre2023:Path,manifest:Path,holdout_cache:Path,output_dir:Path)->dict:
    output_dir.mkdir(parents=True,exist_ok=True)
    ohlcv,quality=load_governed_ohlcv(pre2023,manifest,holdout_cache)
    baseline,base_audit=build_weekly_meta_signals(ohlcv)
    candidates,cand_audit=build_pre_meta_candidates(ohlcv)
    labeled=add_outcomes(candidates,ohlcv)
    wf,train_audit=walkforward_signals(candidates,labeled)
    feature_tickers=set(baseline['ticker'])|set(wf['ticker']); features=add_antifp_features(ohlcv[ohlcv['ticker'].isin(feature_tickers)].copy())
    base_conf,base_j1=apply_j1_confirmation(baseline,features); wf_conf,wf_j1=apply_j1_confirmation(wf,features)
    rows=[]
    for name,s in [('BASELINE_UNTRAINED',base_conf),('META_WALKFORWARD',wf_conf)]:
        for segment,start,end in [('DEVELOPMENT_2010_2022',None,HOLDOUT_START),('HOLDOUT_2023_2026',HOLDOUT_START,None)]:
            m=_segment_run(s,ohlcv,start,end); m.update({'model':name,'segment':segment}); rows.append(m)
    comp=pd.DataFrame(rows)
    summary={
        'status':'SUCCESS','version':'TABPORT_META_WALKFORWARD_RESEARCH_V1','production_promotion':False,
        'governance':{'development':'EXPANDING_WALK_FORWARD_2010_2022','label_embargo_days':EMBARGO_DAYS,'holdout':'2023_2026_EVALUATION_ONLY','holdout_training_frozen_before_2023':True,'holdout_used_for_tuning':False,'synthetic_imputation':False},
        'quality':quality,'baseline_audit':base_audit,'candidate_audit':cand_audit,'labeled_training_rows':int(len(labeled)),
        'walkforward_years_trained':int(train_audit['training_status'].eq('TRAINED_PURGED_TEMPORAL_OOS').sum()),
        'walkforward_years_total':int(len(train_audit)),
    }
    comp.to_csv(output_dir/'TABPORT_META_WF_COMPARISON.csv',index=False); train_audit.to_csv(output_dir/'TABPORT_META_WF_TRAIN_AUDIT.csv',index=False)
    wf[['date','ticker','EV_net','prob_meta','meta_model_status','prob_stop_9','mae_model_status','wf_train_cutoff','wf_train_n','wf_trained']].to_csv(output_dir/'TABPORT_META_WF_SIGNALS.csv',index=False)
    base_j1.to_csv(output_dir/'TABPORT_META_WF_BASELINE_J1_AUDIT.csv',index=False); wf_j1.to_csv(output_dir/'TABPORT_META_WF_J1_AUDIT.csv',index=False)
    (output_dir/'TABPORT_META_WF_SUMMARY.json').write_text(json.dumps(summary,indent=2,default=str),encoding='utf-8')
    print(json.dumps(summary,indent=2,default=str)); print('---COMPARISON---'); print(comp.to_csv(index=False)); print('---TRAIN---'); print(train_audit.to_csv(index=False))
    return summary


def main():
    p=argparse.ArgumentParser(); p.add_argument('--pre2023',required=True); p.add_argument('--manifest',required=True); p.add_argument('--holdout-cache',required=True); p.add_argument('--output-dir',required=True)
    a=p.parse_args(); run(Path(a.pre2023),Path(a.manifest),Path(a.holdout_cache),Path(a.output_dir))
if __name__=='__main__': main()
