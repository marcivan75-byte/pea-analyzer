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
    s=s[(s.obs>=MIN_OBS)&s.mom.notna()]
    s['rank']=s.groupby('signal_date').mom.rank(method='first',ascending=False)
    return s[s['rank']<=TOP_N].sort_values(['signal_date','rank','isin'])


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--history',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    z=norm(pd.read_parquet(a.history)); s=signals(z)
    market_dates=pd.DatetimeIndex(sorted(z.date.unique()))
    quoted={(pd.Timestamp(d),i) for d,i in zip(z.date,z['isin'])}
    rows=[]
    for sd,g in s.groupby('signal_date'):
        future=market_dates[market_dates>pd.Timestamp(sd)]
        if len(future)==0: continue
        ed=pd.Timestamp(future[0])
        for _,r in g.iterrows():
            rows.append({'signal_date':str(pd.Timestamp(sd).date()),'execution_date':str(ed.date()),'isin':r['isin'],'rank':float(r['rank']),'mom':float(r['mom']),'quoted_on_next_market_date':(ed,r['isin']) in quoted})
    d=pd.DataFrame(rows)
    miss=d[~d.quoted_on_next_market_date].copy()
    by_month=d.groupby('signal_date').agg(selected=('isin','size'),missing=('quoted_on_next_market_date',lambda x:int((~x).sum()))).reset_index()
    report={
      'version':'V23_MOMENTUM_TRADABILITY_AUDIT_1',
      'purpose':'diagnostic only; quantify whether frozen top-10 names can actually be executed on the next market date',
      'governance':{'strategy_tuning':False,'holdout_2023_2026_accessed':False,'future_quote_information_used_for_audit_only':True,'survivorship_bias':True},
      'selected_rows':int(len(d)),'missing_next_market_quote_rows':int(len(miss)),
      'missing_rate':float(len(miss)/len(d)) if len(d) else None,
      'months_with_any_missing':int((by_month.missing>0).sum()),
      'months_total':int(len(by_month)),
      'max_missing_in_one_month':int(by_month.missing.max()) if len(by_month) else 0,
      'first_missing_examples':miss.head(30).to_dict(orient='records'),
      'conclusion_rule':'If missing executions are non-trivial and delisting/corporate-action returns are absent, price-only stock-picking portfolio CAGR is not promotable.'
    }
    a.out_dir.mkdir(parents=True,exist_ok=True)
    d.to_csv(a.out_dir/'MOMENTUM_TRADABILITY_ROWS_PRE2023.csv',index=False)
    by_month.to_csv(a.out_dir/'MOMENTUM_TRADABILITY_BY_MONTH_PRE2023.csv',index=False)
    (a.out_dir/'MOMENTUM_TRADABILITY_AUDIT_PRE2023.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
