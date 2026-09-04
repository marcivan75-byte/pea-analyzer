from __future__ import annotations

import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

CUTOFF=pd.Timestamp('2023-01-01')
TOP_N=10
LOOKBACK=252
SKIP=21
MIN_OBS=253
WINDOWS=(1,3,5,10)


def norm(df):
    m={str(c).strip().lower():c for c in df.columns}
    def pick(*xs):
        for x in xs:
            if x in m: return m[x]
        raise SystemExit(f'BLOCK_SCHEMA {xs}')
    z=pd.DataFrame({
        'isin':df[pick('isin')].astype(str),
        'date':pd.to_datetime(df[pick('date','datetime')],errors='coerce').dt.tz_localize(None).dt.normalize(),
        'close':pd.to_numeric(df[pick('close','adj_close','adjusted_close')],errors='coerce')
    }).dropna()
    z=z[(z.close>0)&(z.date<CUTOFF)].drop_duplicates(['isin','date'],keep='last').sort_values(['isin','date'])
    if z.empty or z.date.max()>=CUTOFF: raise SystemExit('BLOCK_GOVERNANCE')
    return z


def signals(z):
    parts=[]
    for isin,g in z.groupby('isin',sort=False):
        g=g.sort_values('date').copy()
        g['mom']=g.close.shift(SKIP)/g.close.shift(LOOKBACK)-1
        g['obs']=np.arange(len(g))+1
        g['month']=g.date.dt.to_period('M')
        parts.append(g[['isin','date','mom','obs','month']])
    x=pd.concat(parts,ignore_index=True)
    anchors=x.groupby('month',as_index=False).date.max().rename(columns={'date':'signal_date'})
    last=x.sort_values(['isin','month','date']).groupby(['isin','month'],as_index=False).tail(1)
    s=last.merge(anchors,on='month',how='left')
    s=s[(s.obs>=MIN_OBS)&s.mom.notna()&(s.date<=s.signal_date)].copy()
    s['rank']=s.groupby('signal_date').mom.rank(method='first',ascending=False)
    return s[s['rank']<=TOP_N].sort_values(['signal_date','rank','isin'])


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--history',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    z=norm(pd.read_parquet(a.history)); s=signals(z)
    quote_dates={isin:pd.DatetimeIndex(g.date.sort_values().unique()) for isin,g in z.groupby('isin')}
    rows=[]
    for _,r in s.iterrows():
        sd=pd.Timestamp(r['signal_date']); isin=str(r['isin']); qd=quote_dates.get(isin,pd.DatetimeIndex([]))
        fut=qd[qd>sd]
        first=pd.Timestamp(fut[0]) if len(fut) else pd.NaT
        delay=None if pd.isna(first) else int((first-sd).days)
        row={'signal_date':str(sd.date()),'feature_date':str(pd.Timestamp(r['date']).date()),'isin':isin,'rank':float(r['rank']),'mom':float(r['mom']),
             'first_quote_after_signal':None if pd.isna(first) else str(first.date()),'delay_calendar_days':delay,'has_any_future_pre2023_quote':bool(len(fut))}
        for w in WINDOWS:
            row[f'executable_within_{w}cd']=bool(delay is not None and delay<=w)
        rows.append(row)
    d=pd.DataFrame(rows)
    if d.empty: raise SystemExit('BLOCK_NO_ROWS')
    by_month=d.groupby('signal_date').agg(selected=('isin','size'),no_future_quote=('has_any_future_pre2023_quote',lambda x:int((~x).sum())),
        gt1=('executable_within_1cd',lambda x:int((~x).sum())),gt3=('executable_within_3cd',lambda x:int((~x).sum())),
        gt5=('executable_within_5cd',lambda x:int((~x).sum())),gt10=('executable_within_10cd',lambda x:int((~x).sum()))).reset_index()
    delays=d.loc[d.has_any_future_pre2023_quote,'delay_calendar_days'].astype(float)
    nofuture=d[~d.has_any_future_pre2023_quote]
    report={
      'version':'V23_MOMENTUM_TRADABILITY_AUDIT_2_PER_SECURITY_NEXT_QUOTE',
      'purpose':'diagnostic only; distinguish exchange-calendar mismatch from true absence of a future executable quote for the frozen top-10 momentum selections',
      'governance':{'strategy_tuning':False,'holdout_2023_2026_accessed':False,'future_quote_information_used_for_audit_only':True,'survivorship_bias':True,'signal_rule_changed':False},
      'selected_rows':int(len(d)),
      'execution_availability':{
        'within_1_calendar_day':int(d.executable_within_1cd.sum()),'within_1_rate':float(d.executable_within_1cd.mean()),
        'within_3_calendar_days':int(d.executable_within_3cd.sum()),'within_3_rate':float(d.executable_within_3cd.mean()),
        'within_5_calendar_days':int(d.executable_within_5cd.sum()),'within_5_rate':float(d.executable_within_5cd.mean()),
        'within_10_calendar_days':int(d.executable_within_10cd.sum()),'within_10_rate':float(d.executable_within_10cd.mean()),
        'no_future_pre2023_quote_rows':int(len(nofuture)),'no_future_rate':float(len(nofuture)/len(d))
      },
      'delay_calendar_days':{'median':None if delays.empty else float(delays.median()),'p95':None if delays.empty else float(delays.quantile(.95)),'max':None if delays.empty else int(delays.max())},
      'months_total':int(len(by_month)),'months_with_any_gt10':int((by_month.gt10>0).sum()),'max_gt10_in_one_month':int(by_month.gt10.max()),
      'no_future_examples':nofuture.head(40).to_dict(orient='records'),
      'interpretation_rule':'Exchange holidays must not be mistaken for delisting. Only per-security quote delays beyond the declared diagnostic windows or complete absence of a later pre-2023 quote indicate a material execution/data problem.',
      'promotion_rule':'Do not promote a stock-picking CAGR from price-only history if non-trivial no-future-quote cases remain unexplained by PIT corporate-action/delisting data.'
    }
    a.out_dir.mkdir(parents=True,exist_ok=True)
    d.to_csv(a.out_dir/'MOMENTUM_TRADABILITY_V2_ROWS_PRE2023.csv',index=False)
    by_month.to_csv(a.out_dir/'MOMENTUM_TRADABILITY_V2_BY_MONTH_PRE2023.csv',index=False)
    (a.out_dir/'MOMENTUM_TRADABILITY_V2_AUDIT_PRE2023.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
