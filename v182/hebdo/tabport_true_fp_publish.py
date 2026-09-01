"""Audit walk-forward du risque de vrai faux positif durable TABPORT.

Un TRUE_FP_DURABLE touche -9 % puis ne recupere jamais le prix d'entree dans
l'horizon de 126 seances. Le score est diagnostique et n'influence aucune decision.
"""
from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss

from v182.hebdo.tabport_publish import read_cache
from v182.hebdo.tabport_walkforward import (
    CalibratedClassifier, _quarter_start, attach_mature_outcomes,
    build_walkforward_candidates,
)


def score_true_fp_walkforward(outcomes:pd.DataFrame)->tuple[pd.DataFrame,pd.DataFrame]:
    work=outcomes.copy(); work['date']=pd.to_datetime(work['date'],utc=True); work['outcome_end_date']=pd.to_datetime(work['outcome_end_date'],utc=True)
    work['quarter_start']=work['date'].map(_quarter_start); scored=[]; vintages=[]
    for q,rows in work.groupby('quarter_start',sort=True):
        hist=work[work['outcome_end_date']<q].copy(); model=CalibratedClassifier('true_fp_durable'); metrics=model.fit(hist)
        v={'quarter_start':str(q),'history_rows':int(len(hist)),'history_true_fp':int(pd.to_numeric(hist.get('true_fp_durable'),errors='coerce').fillna(0).sum()) if len(hist) else 0,
           'model':metrics,'status':'BLOCKED'}
        if model.status!='CALIBRATED_PURGED_TEMPORAL_OOS': vintages.append(v); continue
        s=rows.copy(); s['prob_true_fp_durable']=model.predict(s); y=s['true_fp_durable'].astype(int).to_numpy(); p=s['prob_true_fp_durable'].to_numpy(float)
        prevalence=float(hist['true_fp_durable'].mean()); brier=float(brier_score_loss(y,p)); naive=float(np.mean((y-prevalence)**2))
        cutoff=float(s['prob_true_fp_durable'].quantile(.75)); top=s[s['prob_true_fp_durable']>=cutoff]; base_rate=float(s['true_fp_durable'].mean()); top_rate=float(top['true_fp_durable'].mean()) if len(top) else 0.0
        v.update({'status':'SCORED_OOS','scored_rows':int(len(s)),'true_fp':int(y.sum()),'brier':brier,'naive_brier':naive,
                  'quarter_oos_brier_edge':bool(brier<naive),'top_quartile_true_fp_rate':top_rate,'base_true_fp_rate':base_rate,
                  'top_quartile_lift':None if base_rate<=0 else top_rate/base_rate})
        s['model_vintage']=str(q); scored.append(s); vintages.append(v)
    return (pd.concat(scored,ignore_index=True) if scored else pd.DataFrame(),pd.DataFrame(vintages))


def publish(cache_dir:str|Path,output_dir:str|Path)->dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); ohlcv,_=read_cache(cache_dir)
    candidates=build_walkforward_candidates(ohlcv); outcomes=attach_mature_outcomes(candidates,ohlcv); scores,vintages=score_true_fp_walkforward(outcomes)
    outcomes.to_csv(out/'TABPORT_TRUE_FP_MATURE_OUTCOMES.csv',index=False); vintages.to_csv(out/'TABPORT_TRUE_FP_VINTAGES.csv',index=False)
    if not scores.empty: scores.to_csv(out/'TABPORT_TRUE_FP_OOS_SCORES.csv',index=False)
    valid=vintages[vintages['status'].eq('SCORED_OOS')] if not vintages.empty else pd.DataFrame(); edge=int(valid['quarter_oos_brier_edge'].fillna(False).sum()) if len(valid) else 0
    true_fp=int(pd.to_numeric(outcomes['true_fp_durable'],errors='coerce').fillna(0).sum()); stopped=int(outcomes['hit_stop'].astype(bool).sum())
    diag={'status':'PUBLISHED' if len(valid) else 'BLOCKED','name':'TABPORT_TRUE_FP_DURABLE_WALKFORWARD','decision_influence':False,'retuning':False,'holdout_unlocked':False,
          'definition':'hit fixed -9% stop and never recover entry within original 126-session horizon','mature_outcomes':int(len(outcomes)),
          'stopped_candidates':stopped,'true_fp_durable':true_fp,'stopped_but_recovered':int(stopped-true_fp),'scored_oos_vintages':int(len(valid)),
          'vintages_with_oos_brier_edge':edge,'all_scored_vintages_have_edge':bool(len(valid)>0 and edge==len(valid)),
          'consensus_revision_feature':'SHADOW_FUTURE_PIT_ARCHIVE_NOT_YET_MATURE','promotion':'FORBIDDEN_DIAGNOSTIC_ONLY',
          'vintages':vintages.to_dict(orient='records')}
    (out/'TABPORT_TRUE_FP_DIAGNOSTIC.json').write_text(json.dumps(diag,indent=2,default=str),encoding='utf-8'); print(json.dumps(diag,default=str)); return diag


def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',default='data/cache/actions'); p.add_argument('--output-dir',default='outputs/tabport_true_fp'); a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=='__main__': main()
