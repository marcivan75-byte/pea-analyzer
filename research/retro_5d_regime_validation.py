from __future__ import annotations

import itertools, json
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf
from research.retro_5d_portfolio_validation import load_base, load_bench, features, build_trade_paths, trade_table
from research.retro_5d_portfolio_ranking_validation import portfolio_ranked

OUT=Path('outputs/retro_5d_regime_validation'); OUT.mkdir(parents=True,exist_ok=True)
SETUPS=[('TP20',3,'RS_DESC'),('TP20',5,'RS_DESC'),('DYN_BE_TP20',3,'GAP_ASC'),('DYN_BE_TP20',5,'RS_DESC'),('SL10_TP20',5,'RS_DESC')]


def benchmark_regimes(a,b):
    z=yf.download('^STOXX50E',start=(a-pd.Timedelta(days=300)).strftime('%Y-%m-%d'),end=(b+pd.Timedelta(days=10)).strftime('%Y-%m-%d'),auto_adjust=False,repair=False,progress=False,threads=False)
    if isinstance(z.columns,pd.MultiIndex): z.columns=z.columns.get_level_values(0)
    q=pd.DataFrame({'signal_date':pd.to_datetime(z.index).tz_localize(None),'mkt_close':pd.to_numeric(z.Close,errors='coerce').to_numpy()}).dropna().sort_values('signal_date')
    q['ma20']=q.mkt_close.rolling(20,min_periods=20).mean(); q['ma50']=q.mkt_close.rolling(50,min_periods=50).mean(); q['ma200']=q.mkt_close.rolling(200,min_periods=200).mean()
    q['ret20']=(q.mkt_close/q.mkt_close.shift(20)-1)*100; q['ret60']=(q.mkt_close/q.mkt_close.shift(60)-1)*100
    q['vol20']=q.mkt_close.pct_change().rolling(20,min_periods=20).std(ddof=0)*np.sqrt(252)*100
    return q


def regime_masks(q):
    return {
        'ALL': pd.Series(True,index=q.index),
        'ABOVE_MA20': q.mkt_close>q.ma20,
        'ABOVE_MA50': q.mkt_close>q.ma50,
        'ABOVE_MA200': q.mkt_close>q.ma200,
        'MA20_GT_MA50': q.ma20>q.ma50,
        'RET20_POS': q.ret20>0,
        'RET60_POS': q.ret60>0,
        'VOL20_LT15': q.vol20<15,
        'VOL20_LT20': q.vol20<20,
        'VOL20_LT25': q.vol20<25,
        'VOL20_GE15': q.vol20>=15,
        'VOL20_GE20': q.vol20>=20,
    }


def candidate_defs():
    singles=['ABOVE_MA20','ABOVE_MA50','ABOVE_MA200','MA20_GT_MA50','RET20_POS','RET60_POS','VOL20_LT15','VOL20_LT20','VOL20_LT25','VOL20_GE15','VOL20_GE20']
    defs=[('ALL',)]+[(x,) for x in singles]
    trend={'ABOVE_MA20','ABOVE_MA50','ABOVE_MA200','MA20_GT_MA50','RET20_POS','RET60_POS'}
    vol={'VOL20_LT15','VOL20_LT20','VOL20_LT25','VOL20_GE15','VOL20_GE20'}
    defs += [(a,b) for a in trend for b in vol]
    return defs


def apply_regime(tt,reg,parts):
    keep=np.ones(len(tt),dtype=bool)
    for p in parts:
        if p=='ALL': continue
        keep &= reg[p].fillna(False).to_numpy(bool)
    return tt.loc[keep].copy()


def eval_period(tt,rule,maxp,lo,hi):
    q=tt[(tt.entry_date.dt.year>=lo)&(tt.entry_date.dt.year<=hi)].copy(); a,final,skipped=portfolio_ranked(q,rule,maxp)
    return {'signals':len(q),'accepted':len(a),'skipped':skipped,'return_pct':100*(final/100000-1),'mean_trade_pct':100*a.net_ret.mean() if len(a) else np.nan,'win_rate_pct':100*(a.net_ret>0).mean() if len(a) else np.nan}


def main():
    raw=load_base(); x=features(raw,load_bench(raw.date.min(),raw.date.max())); paths=build_trade_paths(x)
    m=benchmark_regimes(raw.date.min(),raw.date.max())
    defs=candidate_defs(); rows=[]; selected=[]; annual=[]
    for cfg,maxp,rule in SETUPS:
        tt=trade_table(paths,cfg,25).merge(m,on='signal_date',how='left')
        masks=regime_masks(tt); candidates=[]
        for parts in defs:
            z=apply_regime(tt,masks,parts)
            d=eval_period(z,rule,maxp,2010,2018); v=eval_period(z,rule,maxp,2019,2022); o=eval_period(z,rule,maxp,2023,2026)
            rec={'config':cfg,'max_positions':maxp,'ranking_rule':rule,'regime':'+'.join(parts),
                 **{f'DISC_{k}':val for k,val in d.items()},**{f'VAL_{k}':val for k,val in v.items()},**{f'OOS_{k}':val for k,val in o.items()}}
            rec['robust_pre_return_pct']=min(rec['DISC_return_pct'],rec['VAL_return_pct']); rows.append(rec)
            if rec['DISC_accepted']>=30 and rec['VAL_accepted']>=30 and rec['DISC_return_pct']>0 and rec['VAL_return_pct']>0: candidates.append(rec)
        cand=pd.DataFrame(candidates)
        if len(cand): chosen=cand.sort_values(['robust_pre_return_pct','VAL_return_pct','DISC_return_pct'],ascending=False).iloc[0]
        else: chosen=pd.DataFrame([r for r in rows if r['config']==cfg and r['max_positions']==maxp]).sort_values('robust_pre_return_pct',ascending=False).iloc[0]
        selected.append(chosen.to_dict()); parts=tuple(chosen.regime.split('+')); z=apply_regime(tt,masks,parts)
        for y in range(2023,2027):
            yy=z[z.entry_date.dt.year==y].copy(); a,final,skip=portfolio_ranked(yy,rule,maxp)
            annual.append({'year':y,'config':cfg,'max_positions':maxp,'ranking_rule':rule,'regime':chosen.regime,'signals':len(yy),'accepted':len(a),'skipped':skip,'return_pct':100*(final/100000-1),'wins':int((a.net_ret>0).sum()) if len(a) else 0,'win_rate_pct':100*(a.net_ret>0).mean() if len(a) else np.nan,'mean_trade_pct':100*a.net_ret.mean() if len(a) else np.nan})
    pd.DataFrame(rows).to_csv(OUT/'REGIME_GRID_DISC_VAL_OOS.csv',index=False)
    pd.DataFrame(selected).to_csv(OUT/'PRE_SELECTED_REGIME_BY_SETUP.csv',index=False)
    pd.DataFrame(annual).to_csv(OUT/'OOS_ANNUAL_PRE_SELECTED_REGIME.csv',index=False)
    summary={'selection':'regime chosen only from DISC 2010-2018 and VAL 2019-2022; >=30 accepted trades in each; both returns positive; maximize minimum pre-period return','friction_bps_oneway':25,'setups':SETUPS,'candidate_regimes':len(defs),'warning':'2023-2026 is evaluation evidence only, no longer pristine untouched holdout','selected':selected}
    (OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2,default=float),encoding='utf-8'); print(json.dumps(summary,indent=2,default=float))

if __name__=='__main__': main()
