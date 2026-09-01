"""Tie-break de convexite continue walk-forward.

Objectif: conserver la convexite des gros gagnants sans dependre d'un label rare >=20%.
Le risque de stop reste un classifieur calibre. L'upside est une regression continue
sur le rendement positif mature, validee OOS contre la moyenne historique naive.
Le score n'intervient qu'en epsilon lorsque l'EV primaire est identique.
"""
from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_publish import read_cache, build_weekly_meta_signals
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary
from v182.hebdo.tabport_walkforward import (
    build_walkforward_candidates, attach_mature_outcomes, CalibratedClassifier,
    _quarter_start, FEATURES, EMBARGO_DAYS,
)

EPS=1e-7
FEE=0.003
MIN_SPLIT=20


class PurgedUpsideRegressor:
    def __init__(self):
        self.model=None; self.status='UNTRAINED'; self.metrics=None

    def fit(self,history:pd.DataFrame)->dict:
        work=history.copy().sort_values('date').reset_index(drop=True)
        if len(work)<90:
            self.status='BLOCK_TOO_FEW_ROWS'; return {'status':self.status,'n':len(work)}
        times=pd.to_datetime(work['date'],utc=True); start=times.min(); end=times.max(); embargo=pd.Timedelta(days=EMBARGO_DAYS)
        effective=(end-start)-2*embargo
        if effective<=pd.Timedelta(0):
            self.status='BLOCK_TOO_SHORT_TEMPORAL_SPAN'; return {'status':self.status,'n':len(work)}
        train_end=start+effective*0.60; cal_start=train_end+embargo; cal_end=cal_start+effective*0.20; test_start=cal_end+embargo
        train=work[times<=train_end].copy(); cal=work[(times>=cal_start)&(times<=cal_end)].copy(); test=work[times>=test_start].copy()
        if min(len(train),len(cal),len(test))<MIN_SPLIT:
            self.status='BLOCK_SPLIT_TOO_SMALL'; return {'status':self.status,'n':len(work),'n_train':len(train),'n_cal':len(cal),'n_test':len(test)}
        for part in (train,cal,test):
            if part[FEATURES].isna().any().any():
                self.status='BLOCK_FEATURE_NA'; return {'status':self.status,'n':len(work)}
        ytrain=pd.to_numeric(train['outcome_return'],errors='coerce').clip(lower=0,upper=1.5)
        ytest=pd.to_numeric(test['outcome_return'],errors='coerce').clip(lower=0,upper=1.5)
        if ytrain.isna().any() or ytest.isna().any():
            self.status='BLOCK_TARGET_NA'; return {'status':self.status,'n':len(work)}
        model=RandomForestRegressor(n_estimators=160,max_depth=6,min_samples_leaf=20,random_state=42,n_jobs=-1)
        model.fit(train[FEATURES],ytrain)
        pred=np.clip(model.predict(test[FEATURES]),0,1.5)
        naive=np.full(len(test),float(ytrain.mean()))
        mae=float(mean_absolute_error(ytest,pred)); naive_mae=float(mean_absolute_error(ytest,naive))
        rmse=float(mean_squared_error(ytest,pred)**0.5); naive_rmse=float(mean_squared_error(ytest,naive)**0.5)
        # Exiger un avantage sur les deux metriques: sinon le modele n'est pas exploite.
        if not (mae<naive_mae and rmse<naive_rmse):
            self.status='BLOCK_NO_OOS_REGRESSION_EDGE'
            return {'status':self.status,'n':len(work),'n_train':len(train),'n_cal':len(cal),'n_test':len(test),
                    'mae':mae,'naive_mae':naive_mae,'rmse':rmse,'naive_rmse':naive_rmse}
        self.model=model; self.status='CALIBRATED_PURGED_TEMPORAL_OOS'
        self.metrics={'status':self.status,'n':len(work),'n_train':len(train),'n_cal':len(cal),'n_test':len(test),
                      'mae':mae,'naive_mae':naive_mae,'rmse':rmse,'naive_rmse':naive_rmse,
                      'train_end':str(train_end),'cal_start':str(cal_start),'cal_end':str(cal_end),'test_start':str(test_start),
                      'embargo_days':EMBARGO_DAYS}
        return self.metrics

    def predict(self,df:pd.DataFrame)->np.ndarray:
        if self.model is None: raise ValueError('BLOCK_CONT_CONVEXITY: upside regressor not validated')
        return np.clip(self.model.predict(df[FEATURES]),0,1.5)


def continuous_scores(outcomes:pd.DataFrame)->tuple[pd.DataFrame,pd.DataFrame]:
    work=outcomes.copy(); work['date']=pd.to_datetime(work['date'],utc=True); work['outcome_end_date']=pd.to_datetime(work['outcome_end_date'],utc=True)
    work['quarter_start']=work['date'].map(_quarter_start)
    scored=[]; vintages=[]
    for q,score_rows in work.groupby('quarter_start',sort=True):
        hist=work[work['outcome_end_date']<q].copy()
        stop=CalibratedClassifier('hit_stop'); upside=PurgedUpsideRegressor()
        sm=stop.fit(hist); um=upside.fit(hist)
        row={'quarter_start':str(q),'history_rows':int(len(hist)),'stop_model':sm,'upside_model':um,'status':'BLOCKED'}
        if stop.status!='CALIBRATED_PURGED_TEMPORAL_OOS' or upside.status!='CALIBRATED_PURGED_TEMPORAL_OOS':
            vintages.append(row); continue
        stop_returns=pd.to_numeric(hist.loc[hist['hit_stop'].astype(bool),'outcome_return'],errors='coerce').dropna()
        nonstop_returns=pd.to_numeric(hist.loc[~hist['hit_stop'].astype(bool),'outcome_return'],errors='coerce').dropna()
        if len(stop_returns)<20 or len(nonstop_returns)<20:
            row['status']='BLOCK_PAYOFF_SUPPORT'; vintages.append(row); continue
        avg_stop=float(stop_returns.mean()); base_nonstop=float(nonstop_returns.clip(lower=-0.05,upper=0.20).mean())
        s=score_rows.copy(); pstop=stop.predict(s); pup=upside.predict(s)
        # Convexite continue: upside attendu, moins cout probabilise du stop; terme neutre conserve petit.
        s['prob_stop_9_calibrated']=pstop; s['pred_upside_positive']=pup
        s['continuous_convexity_ev']=(1-pstop)*pup + pstop*avg_stop + (1-pstop)*0.10*base_nonstop - FEE
        s['continuous_model_vintage']=str(q); s['continuous_model_status']='CALIBRATED_PURGED_TEMPORAL_OOS'
        row.update({'status':'VALIDATED','scored_rows':int(len(s)),'avg_stop':avg_stop,'base_nonstop':base_nonstop})
        vintages.append(row); scored.append(s)
    return (pd.concat(scored,ignore_index=True).sort_values(['date','ticker']).reset_index(drop=True) if scored else pd.DataFrame(),pd.DataFrame(vintages))


def _rank_tie(df:pd.DataFrame)->pd.DataFrame:
    x=df.copy(); base=pd.to_numeric(x['EV_net'],errors='coerce'); sec=pd.to_numeric(x['continuous_convexity_ev'],errors='coerce')
    if base.isna().any() or sec.isna().any(): raise ValueError('BLOCK_CONT_CONVEXITY: incomplete rank inputs')
    lo=float(sec.min()); hi=float(sec.max()); norm=(sec-lo)/(hi-lo) if hi>lo else pd.Series(0.5,index=x.index)
    x['EV_net_original']=base; x['EV_net']=base+EPS*norm; x['tiebreak_mode']='CONTINUOUS_CONVEXITY_EV'
    return x


def _summary(name,result):
    s=overall_summary(result['ledger'],result['equity'],65000.0); s['scenario']=name; return s


def publish(cache_dir:str|Path,output_dir:str|Path)->dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    ohlcv,_=read_cache(cache_dir)
    candidates=build_walkforward_candidates(ohlcv); outcomes=attach_mature_outcomes(candidates,ohlcv)
    scores,vintages=continuous_scores(outcomes); vintages.to_csv(out/'TABPORT_CONT_CONVEXITY_VINTAGES.csv',index=False)
    if scores.empty: raise ValueError('BLOCK_CONT_CONVEXITY: no validated vintage')
    scores[['date','ticker','prob_stop_9_calibrated','pred_upside_positive','continuous_convexity_ev','continuous_model_vintage','continuous_model_status']].to_csv(out/'TABPORT_CONT_CONVEXITY_SCORES.csv',index=False)
    first=pd.to_datetime(scores['date'],utc=True).min(); last=pd.to_datetime(scores['date'],utc=True).max()
    base,_=build_weekly_meta_signals(ohlcv); base=base[(pd.to_datetime(base['date'],utc=True)>=first)&(pd.to_datetime(base['date'],utc=True)<=last)].copy()
    features=add_antifp_features(ohlcv[ohlcv['ticker'].astype(str).isin(set(base['ticker'].astype(str)))].copy())
    confirmed,audit=apply_j1_confirmation(base,features); audit.to_csv(out/'TABPORT_CONT_CONVEXITY_CONFIRMATION_AUDIT.csv',index=False)
    if confirmed.empty: raise ValueError('BLOCK_CONT_CONVEXITY: baseline confirmation empty')
    confirmed['score_key_date']=pd.to_datetime(confirmed['original_signal_date'],utc=True)
    skey=scores[['date','ticker','prob_stop_9_calibrated','pred_upside_positive','continuous_convexity_ev']].copy().rename(columns={'date':'score_key_date'})
    merged=confirmed.merge(skey,on=['score_key_date','ticker'],how='left',validate='many_to_one')
    coverage=float(merged['continuous_convexity_ev'].notna().mean()); merged.to_csv(out/'TABPORT_CONT_CONVEXITY_BASELINE_CONFIRMED.csv',index=False)
    if coverage<0.95: raise ValueError(f'BLOCK_CONT_CONVEXITY: coverage {coverage:.2%}')
    plain=ohlcv[['date','ticker','open','high','low','close']].copy(); cfg=TabportConfig()
    baseline=Tabport65k(cfg).run(merged,plain); ranked=Tabport65k(cfg).run(_rank_tie(merged),plain)
    rows=[_summary('BASELINE_TICKER_TIE',baseline),_summary('CONTINUOUS_CONVEXITY_TIE',ranked)]
    pd.DataFrame(rows).to_csv(out/'TABPORT_CONT_CONVEXITY_COMPARISON.csv',index=False)
    for name,res in [('baseline',baseline),('continuous',ranked)]:
        d=out/name; d.mkdir(parents=True,exist_ok=True); res['ledger'].to_csv(d/'ledger.csv',index=False); res['equity'].to_csv(d/'nav.csv',index=False)
    b,r=rows
    valid=vintages[vintages['status'].eq('VALIDATED')] if 'status' in vintages else pd.DataFrame()
    diag={'status':'PUBLISHED','name':'TABPORT_CONTINUOUS_CONVEXITY_TIEBREAK','retuning':False,'holdout_unlocked':False,
          'epsilon':EPS,'coverage_pct':coverage*100,'validated_vintages':int(len(valid)),'scored_rows':int(len(scores)),
          'first_score':str(first),'last_score':str(last),'baseline':b,'continuous':r,'delta':{
            'return_pct':float(r['rendement_total_depuis_65000_pct']-b['rendement_total_depuis_65000_pct']),
            'win_rate_pct':float(r['taux_gain_pct']-b['taux_gain_pct']),
            'profit_factor':float(r['profit_factor']-b['profit_factor']),
            'rr_payoff':float(r['rr_payoff']-b['rr_payoff']),
            'expectancy_pct':float(r['esperance_pct']-b['esperance_pct']),
            'stops':int(r['stops']-b['stops']),
            'drawdown_pct':float(r['drawdown_max_pct']-b['drawdown_max_pct'])},
          'principle':'Continuous positive-upside regression plus calibrated stop risk; both validated strictly out-of-sample, used only as tie-break of identical primary EV.'}
    (out/'TABPORT_CONT_CONVEXITY_DIAGNOSTIC.json').write_text(json.dumps(diag,indent=2,default=str),encoding='utf-8')
    print(json.dumps(diag,default=str)); return diag


def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',default='data/cache/actions'); p.add_argument('--output-dir',default='outputs/tabport_continuous_convexity'); a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=='__main__': main()
