"""Publication de la cohorte TABPORT calibrée walk-forward et comparaison temporelle équitable."""
from __future__ import annotations

import argparse, json
from pathlib import Path
import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig
from v182.hebdo.tabport_publish import read_cache, build_weekly_meta_signals
from v182.hebdo.tabport_antifp import add_antifp_features, apply_j1_confirmation
from v182.hebdo.tabport_enriched import overall_summary, period_table
from v182.hebdo.tabport_walkforward import build_walkforward_candidates, attach_mature_outcomes, walkforward_score


def _run(signals:pd.DataFrame,plain:pd.DataFrame,cfg:TabportConfig)->dict:
    if signals.empty: raise ValueError('BLOCK_WALKFORWARD_PUBLISH: empty signals')
    return Tabport65k(cfg).run(signals,plain)


def publish(cache_dir:str|Path,output_dir:str|Path)->dict:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    ohlcv,_=read_cache(cache_dir)
    candidates=build_walkforward_candidates(ohlcv)
    outcomes=attach_mature_outcomes(candidates,ohlcv)
    scored,vintages=walkforward_score(outcomes)
    candidates.to_csv(out/'TABPORT_WF_CANDIDATES.csv',index=False)
    outcomes.to_csv(out/'TABPORT_WF_MATURE_OUTCOMES.csv',index=False)
    vintages.to_csv(out/'TABPORT_WF_MODEL_VINTAGES.csv',index=False)
    if scored.empty:
        diag={'status':'BLOCKED','reason':'NO_VALIDATED_WALKFORWARD_VINTAGE','candidate_rows':len(candidates),'mature_outcomes':len(outcomes),
              'vintages':vintages.to_dict(orient='records')}
        (out/'TABPORT_WF_DIAGNOSTIC.json').write_text(json.dumps(diag,indent=2,default=str),encoding='utf-8')
        print(json.dumps(diag,default=str)); return diag
    scored.to_csv(out/'TABPORT_WF_SCORED_SIGNALS.csv',index=False)

    tickers=set(scored['ticker'].astype(str)); raw=ohlcv[ohlcv['ticker'].astype(str).isin(tickers)].copy(); features=add_antifp_features(raw)
    plain=ohlcv[["date","ticker","open","high","low","close"]].copy()
    confirmed,confirm_audit=apply_j1_confirmation(scored,features)
    confirm_audit.to_csv(out/'TABPORT_WF_CONFIRMATION_AUDIT.csv',index=False)
    confirmed.to_csv(out/'TABPORT_WF_CONFIRMED_SIGNALS.csv',index=False)
    if confirmed.empty:
        raise ValueError('BLOCK_WALKFORWARD_PUBLISH: no calibrated signals survived confirmation')
    cfg=TabportConfig(); calibrated=_run(confirmed,plain,cfg)

    first=pd.to_datetime(scored['date'],utc=True).min(); last=pd.to_datetime(scored['date'],utc=True).max()
    baseline_signals,_=build_weekly_meta_signals(ohlcv)
    baseline_signals=baseline_signals[(pd.to_datetime(baseline_signals['date'],utc=True)>=first)&(pd.to_datetime(baseline_signals['date'],utc=True)<=last)].copy()
    baseline_confirmed,baseline_confirm_audit=apply_j1_confirmation(baseline_signals,add_antifp_features(ohlcv[ohlcv['ticker'].astype(str).isin(set(baseline_signals['ticker'].astype(str)))].copy()))
    baseline_confirm_audit.to_csv(out/'TABPORT_WF_BASELINE_CONFIRMATION_AUDIT.csv',index=False)
    if baseline_confirmed.empty: raise ValueError('BLOCK_WALKFORWARD_PUBLISH: comparable baseline empty')
    baseline=_run(baseline_confirmed,plain,cfg)

    cal_summary=overall_summary(calibrated['ledger'],calibrated['equity'],cfg.initial_cash); base_summary=overall_summary(baseline['ledger'],baseline['equity'],cfg.initial_cash)
    comparison=pd.DataFrame([{'scenario':'BASELINE_CONFIRM_J1_SAME_WINDOW',**base_summary},{'scenario':'WALKFORWARD_CALIBRATED_CONFIRM_J1',**cal_summary}])
    comparison.to_csv(out/'TABPORT_WF_COMPARISON.csv',index=False)
    calibrated['ledger'].to_csv(out/'TABPORT_WF_LEDGER.csv',index=False); calibrated['equity'].to_csv(out/'TABPORT_WF_NAV.csv',index=False)
    period_table(calibrated['ledger'],calibrated['equity'],'Q').to_csv(out/'TABPORT_WF_QUARTERLY.csv',index=False)
    baseline['ledger'].to_csv(out/'TABPORT_WF_BASELINE_LEDGER.csv',index=False)

    valid_v=vintages[vintages['status'].eq('VALIDATED')] if 'status' in vintages else pd.DataFrame()
    diag={
        'status':'PUBLISHED','name':'TABPORT_WALKFORWARD_CALIBRATED_TECHNICAL','retuning':False,'holdout_unlocked':False,
        'preopen_historical':'UNAVAILABLE_NOT_SIMULATED','sector_historical':'UNAVAILABLE_NOT_MODEL_FEATURE',
        'candidate_rows':int(len(candidates)),'mature_outcomes':int(len(outcomes)),'validated_vintages':int(len(valid_v)),
        'scored_signals':int(len(scored)),'confirmed_calibrated_signals':int(len(confirmed)),
        'coverage':{'first_score':str(first),'last_score':str(last)},
        'baseline_same_window':base_summary,'walkforward_calibrated':cal_summary,
        'delta':{
            'return_pct':float(cal_summary['rendement_total_depuis_65000_pct']-base_summary['rendement_total_depuis_65000_pct']),
            'win_rate_pct':float(cal_summary['taux_gain_pct']-base_summary['taux_gain_pct']),
            'profit_factor':float(cal_summary['profit_factor']-base_summary['profit_factor']),
            'rr_payoff':float(cal_summary['rr_payoff']-base_summary['rr_payoff']),
            'expectancy_pct':float(cal_summary['esperance_pct']-base_summary['esperance_pct']),
            'stops':int(cal_summary['stops']-base_summary['stops']),
            'max_drawdown_pct':float(cal_summary['drawdown_max_pct']-base_summary['drawdown_max_pct']),
        },
        'model_acceptance':'Each stop/meta vintage must beat its train-prevalence naive Brier score on a purged temporal test set before scoring.',
    }
    (out/'TABPORT_WF_DIAGNOSTIC.json').write_text(json.dumps(diag,indent=2,default=str),encoding='utf-8')
    print(json.dumps(diag,default=str)); return diag


def main():
    p=argparse.ArgumentParser(); p.add_argument('--cache',default='data/cache/actions'); p.add_argument('--output-dir',default='outputs/tabport_walkforward'); a=p.parse_args(); publish(a.cache,a.output_dir)

if __name__=='__main__': main()
