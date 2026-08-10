from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd
from v182.tct.backtest_v212 import TradeConfig,make_trade_outcomes,evaluate_scores,purged_holdout,purged_walk_forward

def _read(path:Path):
    return pd.read_parquet(path) if path.suffix.lower()=='.parquet' else pd.read_csv(path,sep=';',low_memory=False)

def main():
    p=argparse.ArgumentParser(); p.add_argument('--scores',required=True,type=Path); p.add_argument('--ohlc',required=True,type=Path); p.add_argument('--out',type=Path,default=Path('outputs/backtest/V21.2_TCT_EXPLOSIF_BACKTEST.json')); a=p.parse_args()
    s=_read(a.scores)
    if 'instrument_id' not in s and 'isin' in s:s=s.rename(columns={'isin':'instrument_id'})
    if 'score' not in s and 'tct_score_v212' in s:s=s.rename(columns={'tct_score_v212':'score'})
    s['snapshot_date']=pd.to_datetime(s['snapshot_date'],errors='coerce'); outcomes=make_trade_outcomes(_read(a.ohlc),TradeConfig()); merged=s.merge(outcomes,on=['snapshot_date','instrument_id'],how='inner')
    if merged.empty:raise RuntimeError('No point-in-time score/outcome matches')
    end=merged.snapshot_date.max(); report={'version':'V21.2_TCT_EXPLOSIF_OPT','entry_rule':'NEXT_SESSION_OPEN','same_bar_policy':'STOP_FIRST','windows':{}}
    for months in (12,18,36):
        w=merged[merged.snapshot_date>=end-pd.DateOffset(months=months)].copy(); report['windows'][f'{months}m']={'rows':len(w),'metrics':evaluate_scores(w,70,20),'holdout':purged_holdout(w,70),'walk_forward':purged_walk_forward(w,70)}
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2,default=str))

if __name__=='__main__':main()
