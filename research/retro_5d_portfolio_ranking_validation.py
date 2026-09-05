from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
from research.retro_5d_portfolio_validation import load_base, load_bench, features, build_trade_paths, trade_table

OUT=Path('outputs/retro_5d_portfolio_ranking_validation'); OUT.mkdir(parents=True,exist_ok=True)
RULES=['RS_DESC','CLOSELOC_DESC','GAP_ASC','ADV_DESC','BALANCED']
CFGS=['TP20','SL10_TP20','DYN_BE_TP20']


def score_entries(z,rule):
    q=z.copy()
    if rule=='RS_DESC': return q.sort_values(['rs_rank','close_loc','ticker'],ascending=[False,False,True])
    if rule=='CLOSELOC_DESC': return q.sort_values(['close_loc','rs_rank','ticker'],ascending=[False,False,True])
    if rule=='GAP_ASC': return q.sort_values(['gap_pct','rs_rank','ticker'],ascending=[True,False,True])
    if rule=='ADV_DESC': return q.sort_values(['adv20','rs_rank','ticker'],ascending=[False,False,True])
    # Fixed ex-ante score: leadership + strong close, with mild penalty for extreme gap size.
    q['_score']=0.5*(q.rs_rank/100.0)+0.5*q.close_loc-0.25*np.clip((q.gap_pct-7.5)/20.0,0,1)
    return q.sort_values(['_score','rs_rank','ticker'],ascending=[False,False,True])


def portfolio_ranked(trades,rule,max_pos=5,initial=100000.0):
    t=trades.sort_values(['entry_date','ticker']).copy(); cash=initial; openpos=[]; accepted=[]; skipped=0
    dates=sorted(set(pd.to_datetime(t.entry_date))|set(pd.to_datetime(t.exit_date)))
    entries={pd.Timestamp(d):z for d,z in t.groupby('entry_date')}
    for d in dates:
        d=pd.Timestamp(d)
        if d in entries:
            zz=score_entries(entries[d],rule)
            for _,r in zz.iterrows():
                if any(p['ticker']==r.ticker for p in openpos) or len(openpos)>=max_pos: skipped+=1; continue
                equity_est=cash+sum(p['notional'] for p in openpos); notional=min(cash,equity_est/max_pos)
                if notional<=0: skipped+=1; continue
                cash-=notional; rec=r.to_dict(); rec['notional']=notional; rec['capacity_pct_adv20']=100*notional/max(float(r.adv20),1); rec['ranking_rule']=rule; openpos.append(rec); accepted.append(rec)
        exiting=[p for p in openpos if pd.Timestamp(p['exit_date'])==d]
        for p in exiting:
            cash += p['notional']*(1+p['net_ret']); openpos.remove(p)
    final=cash+sum(p['notional']*(1+p['net_ret']) for p in openpos)
    a=pd.DataFrame(accepted)
    if len(a): a['pnl_eur']=a.notional*a.net_ret
    return a,final,skipped


def stats(tt,rule,maxp,lo,hi):
    q=tt[(tt.entry_date.dt.year>=lo)&(tt.entry_date.dt.year<=hi)].copy(); a,final,skip=portfolio_ranked(q,rule,maxp)
    return {'signals':len(q),'accepted':len(a),'skipped':skip,'return_pct':100*(final/100000-1),'mean_trade_pct':100*a.net_ret.mean() if len(a) else np.nan,'win_rate_pct':100*(a.net_ret>0).mean() if len(a) else np.nan,'final_equity_eur':final},a


def main():
    raw=load_base(); x=features(raw,load_bench(raw.date.min(),raw.date.max())); paths=build_trade_paths(x)
    rows=[]; selections=[]; annual_rows=[]; chosen_details=[]
    for cfg in CFGS:
        tt=trade_table(paths,cfg,25)
        for maxp in [3,5,10]:
            recs=[]
            for rule in RULES:
                d,_=stats(tt,rule,maxp,2010,2018); v,_=stats(tt,rule,maxp,2019,2022); o,_=stats(tt,rule,maxp,2023,2026)
                rec={'config':cfg,'max_positions':maxp,'ranking_rule':rule,
                     **{f'DISC_{k}':v0 for k,v0 in d.items()},**{f'VAL_{k}':v0 for k,v0 in v.items()},**{f'OOS_{k}':v0 for k,v0 in o.items()}}
                rec['robust_pre_return_pct']=min(rec['DISC_return_pct'],rec['VAL_return_pct']); rows.append(rec); recs.append(rec)
            cand=pd.DataFrame(recs).sort_values(['robust_pre_return_pct','VAL_return_pct','DISC_return_pct'],ascending=False)
            positive=cand[(cand.DISC_return_pct>0)&(cand.VAL_return_pct>0)]
            chosen=(positive.iloc[0] if len(positive) else cand.iloc[0]); selections.append(chosen.to_dict())
            rule=chosen.ranking_rule
            for y in range(2023,2027):
                q=tt[tt.entry_date.dt.year==y].copy(); a,final,skip=portfolio_ranked(q,rule,maxp)
                annual_rows.append({'year':y,'config':cfg,'max_positions':maxp,'ranking_rule':rule,'signals':len(q),'accepted':len(a),'skipped':skip,'return_pct':100*(final/100000-1),'wins':int((a.net_ret>0).sum()) if len(a) else 0,'win_rate_pct':100*(a.net_ret>0).mean() if len(a) else np.nan,'mean_trade_pct':100*a.net_ret.mean() if len(a) else np.nan})
                if len(a): chosen_details.append(a.assign(year=y,config=cfg,max_positions=maxp,ranking_rule=rule))
    allr=pd.DataFrame(rows); allr.to_csv(OUT/'RANKING_RULES_DISC_VAL_OOS.csv',index=False)
    sel=pd.DataFrame(selections); sel.to_csv(OUT/'PRE_SELECTED_RANKING_BY_CONFIG_CAPACITY.csv',index=False)
    ann=pd.DataFrame(annual_rows); ann.to_csv(OUT/'OOS_ANNUAL_PRE_SELECTED_RANKING.csv',index=False)
    pd.concat(chosen_details,ignore_index=True).to_csv(OUT/'OOS_TRADES_PRE_SELECTED_RANKING.csv',index=False)
    summary={'selection':'ranking chosen only from 2010-2018 DISC and 2019-2022 VAL; maximize minimum of the two portfolio returns, require both positive when possible','friction_bps_oneway':25,'rules':RULES,'warning':'2023-2026 has already been inspected in prior research passes; results are evaluation evidence, not a pristine untouched holdout','selections':selections}
    (OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2,default=float),encoding='utf-8'); print(json.dumps(summary,indent=2,default=float))

if __name__=='__main__': main()
