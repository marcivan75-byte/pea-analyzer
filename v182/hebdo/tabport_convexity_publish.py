"""Tie-break convexite walk-forward: probabilité de gros gagnant vs risque de stop.

Le score n'est utilise que pour departager des EV primaires identiques. Les labels,
modeles et payoffs sont construits exclusivement avec des outcomes deja matures avant
chaque trimestre. Aucun holdout final n'est deverrouille.
"""
from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_publish import read_cache, build_weekly_meta_signals
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary
from v182.hebdo.tabport_walkforward import build_walkforward_candidates, attach_mature_outcomes, CalibratedClassifier, _quarter_start

BIG_WIN_THRESHOLD=0.20
EPS=1e-7
FEE=0.003


def convexity_scores(outcomes:pd.DataFrame)->tuple[pd.DataFrame,pd.DataFrame]:
    work=outcomes.copy()
    work['date']=pd.to_datetime(work['date'],utc=True); work['outcome_end_date']=pd.to_datetime(work['outcome_end_date'],utc=True)
    work['quarter_start']=work['date'].map(_quarter_start)
    work['big_win']=(pd.to_numeric(work['outcome_return'])>=BIG_WIN_THRESHOLD).astype(int)
    scored=[]; vintages=[]
    for q,score_rows in work.groupby('quarter_start',sort=True):
        hist=work[work['outcome_end_date']<q].copy()
        stop=CalibratedClassifier('hit_stop'); big=CalibratedClassifier('big_win')
        sm=stop.fit(hist); bm=big.fit(hist)
        row={'quarter_start':str(q),'history_rows':int(len(hist)),'stop_model':sm,'big_win_model':bm,'status':'BLOCKED'}
        if stop.status!='CALIBRATED_PURGED_TEMPORAL_OOS' or big.status!='CALIBRATED_PURGED_TEMPORAL_OOS':
            vintages.append(row); continue
        ret=pd.to_numeric(hist['outcome_return'],errors='coerce').dropna()
        big_mask=ret>=BIG_WIN_THRESHOLD; stop_mask=hist.loc[ret.index,'hit_stop'].astype(bool)
        mid_mask=(~big_mask)&(~stop_mask)
        if int(big_mask.sum())<20 or int(stop_mask.sum())<20 or int(mid_mask.sum())<20:
            row['status']='BLOCK_PAYOFF_SUPPORT'; vintages.append(row); continue
        avg_big=float(ret[big_mask].mean()); avg_stop=float(ret[stop_mask].mean()); avg_mid=float(ret[mid_mask].mean())
        s=score_rows.copy(); pstop=stop.predict(s); pbig=big.predict(s)
        total=pstop+pbig; over=total>0.95
        pstop[over]=pstop[over]/total[over]*0.95; pbig[over]=pbig[over]/total[over]*0.95
        pmid=np.maximum(0,1-pstop-pbig)
        s['prob_stop_9_calibrated']=pstop; s['prob_big_win_20']=pbig
        s['convexity_ev']=pbig*avg_big+pstop*avg_stop+pmid*avg_mid-FEE
        s['convexity_model_vintage']=str(q); s['convexity_model_status']='CALIBRATED_PURGED_TEMPORAL_OOS'
        row.update({'status':'VALIDATED','scored_rows':int(len(s)),'avg_big_win':avg_big,'avg_stop':avg_stop,'avg_mid':avg_mid})
        vintages.append(row); scored.append(s)
    return (pd.concat(scored,ignore_index=True).sort_values(['date','ticker']).reset_index(drop=True) if scored else pd.DataFrame(),pd.DataFrame(vintages))


def _rank_tie(df:pd.DataFrame)->pd.DataFrame:
    x=df.copy(); base=pd.to_numeric(x['EV_net'],errors='coerce')
    sec=pd.to_numeric(x['convexity_ev'],errors='coerce')
    if base.isna().any() or sec.isna().any(): raise ValueError('BLOCK_CONVEXITY: incomplete rank inputs')
    lo=float(sec.min()); hi=float(sec.max()); norm=(sec-lo)/(hi-lo) if hi>lo else pd.Series(0.5,index=x.index)
    x['EV_net_original']=base; x['EV_net']=base+EPS*norm; x['tiebreak_mode']='CALIBRATED_CONVEXITY_EV'
    return x


def _summary(name,result):
    s=overall_summary(result['ledger'],result['equity'],65000.0); s['scenario']=name; return s


def publish(cache_dir:str|Path,output_dir:str|Path)->dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    ohlcv,_=read_cache(cache_dir)
    candidates=build_walkforward_candidates(ohlcv); outcomes=attach_mature_outcomes(candidates,ohlcv)
    scores,vintages=convexity_scores(outcomes); vintages.to_csv(out/'TABPORT_CONVEXITY_VINTAGES.csv',index=False)
    if scores.empty: raise ValueError('BLOCK_CONVEXITY: no validated vintage')
    scores[['date','ticker','prob_stop_9_calibrated','prob_big_win_20','convexity_ev','convexity_model_vintage','convexity_model_status']].to_csv(out/'TABPORT_CONVEXITY_SCORES.csv',index=False)
    first=pd.to_datetime(scores['date'],utc=True).min(); last=pd.to_datetime(scores['date'],utc=True).max()
    base,_=build_weekly_meta_signals(ohlcv); base=base[(pd.to_datetime(base['date'],utc=True)>=first)&(pd.to_datetime(base['date'],utc=True)<=last)].copy()
    features=add_antifp_features(ohlcv[ohlcv['ticker'].astype(str).isin(set(base['ticker'].astype(str)))].copy())
    confirmed,audit=apply_j1_confirmation(base,features); audit.to_csv(out/'TABPORT_CONVEXITY_CONFIRMATION_AUDIT.csv',index=False)
    if confirmed.empty: raise ValueError('BLOCK_CONVEXITY: baseline confirmation empty')
    confirmed['score_key_date']=pd.to_datetime(confirmed['original_signal_date'],utc=True)
    skey=scores[['date','ticker','prob_stop_9_calibrated','prob_big_win_20','convexity_ev']].copy().rename(columns={'date':'score_key_date'})
    merged=confirmed.merge(skey,on=['score_key_date','ticker'],how='left',validate='many_to_one')
    coverage=float(merged['convexity_ev'].notna().mean()); merged.to_csv(out/'TABPORT_CONVEXITY_BASELINE_CONFIRMED.csv',index=False)
    if coverage<0.95: raise ValueError(f'BLOCK_CONVEXITY: coverage {coverage:.2%}')
    plain=ohlcv[['date','ticker','open','high','low','close']].copy(); cfg=TabportConfig()
    baseline=Tabport65k(cfg).run(merged,plain); ranked=Tabport65k(cfg).run(_rank_tie(merged),plain)
    rows=[_summary('BASELINE_TICKER_TIE',baseline),_summary('CALIBRATED_CONVEXITY_TIE',ranked)]
    pd.DataFrame(rows).to_csv(out/'TABPORT_CONVEXITY_COMPARISON.csv',index=False)
    for name,res in [('baseline',baseline),('convexity',ranked)]:
        d=out/name; d.mkdir(parents=True,exist_ok=True); res['ledger'].to_csv(d/'ledger.csv',index=False); res['equity'].to_csv(d/'nav.csv',index=False)
    b,r=rows
    diag={'status':'PUBLISHED','name':'TABPORT_CALIBRATED_CONVEXITY_TIEBREAK','retuning':False,'holdout_unlocked':False,
          'big_win_threshold':BIG_WIN_THRESHOLD,'epsilon':EPS,'coverage_pct':coverage*100,'primary_ev_unique_values':int(merged['EV_net'].nunique()),
          'baseline':b,'convexity':r,'delta':{
            'return_pct':float(r['rendement_total_depuis_65000_pct']-b['rendement_total_depuis_65000_pct']),
            'win_rate_pct':float(r['taux_gain_pct']-b['taux_gain_pct']),
            'profit_factor':float(r['profit_factor']-b['profit_factor']),
            'rr_payoff':float(r['rr_payoff']-b['rr_payoff']),
            'expectancy_pct':float(r['esperance_pct']-b['esperance_pct']),
            'stops':int(r['stops']-b['stops']),
            'drawdown_pct':float(r['drawdown_max_pct']-b['drawdown_max_pct'])},
          'principle':'Model downside and right-tail upside separately; use calibrated convexity only as tie-break while primary EV is equal.'}
    (out/'TABPORT_CONVEXITY_DIAGNOSTIC.json').write_text(json.dumps(diag,indent=2,default=str),encoding='utf-8')
    print(json.dumps(diag,default=str)); return diag


def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',default='data/cache/actions'); p.add_argument('--output-dir',default='outputs/tabport_convexity'); a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=='__main__': main()
