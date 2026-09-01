from __future__ import annotations

import argparse, json
from pathlib import Path
import pandas as pd

ANCHOR = pd.Timestamp('2019-12-31')
CUTOFF = pd.Timestamp('2023-01-01')
ISINS = [
    'DK0060520450','FR0011716265','IT0001469995','IT0003895668','NL0000334118',
    'NO0010159684','NO0010205966','NO0010598683','NO0010708068'
]


def normalize(df):
    m={str(c).strip().lower():c for c in df.columns}
    def pick(*xs):
        for x in xs:
            if x in m: return m[x]
        raise SystemExit(f'BLOCK_SCHEMA {xs}')
    z=pd.DataFrame({
        'isin':df[pick('isin')].astype(str),
        'date':pd.to_datetime(df[pick('date','datetime')], errors='coerce').dt.tz_localize(None).dt.normalize(),
        'close':pd.to_numeric(df[pick('close','adj_close','adjusted_close')],errors='coerce')
    }).dropna()
    return z[(z.close>0)&(z.date<CUTOFF)].drop_duplicates(['isin','date'],keep='last').sort_values(['isin','date'])


def one(g):
    pre=g[g.date<=ANCHOR]
    post=g[g.date>ANCHOR]
    last_pre=pre.date.max() if len(pre) else pd.NaT
    next_post=post.date.min() if len(post) else pd.NaT
    row={'isin':str(g.isin.iloc[0]),'last_pre_anchor':None if pd.isna(last_pre) else str(last_pre.date()),
         'days_since_last_quote_at_anchor':None if pd.isna(last_pre) else int((ANCHOR-last_pre).days),
         'next_quote_after_anchor':None if pd.isna(next_post) else str(next_post.date()),
         'days_to_next_quote':None if pd.isna(next_post) else int((next_post-ANCHOR).days),
         'last_pre2023_quote':str(g.date.max().date()),'resumes_after_anchor':bool(len(post))}
    for d in [20,40,60,120,252]:
        a=ANCHOR-pd.Timedelta(days=d)
        row[f'quotes_prev_{d}cd']=int(((pre.date> a)&(pre.date<=ANCHOR)).sum())
    # trailing inter-quote gap stats known at anchor
    p=pre.tail(120).copy()
    gaps=p.date.diff().dt.days.dropna()
    row['max_gap_prev120obs_cd']=None if gaps.empty else int(gaps.max())
    row['median_gap_prev120obs_cd']=None if gaps.empty else float(gaps.median())
    return row


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--history',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    z=normalize(pd.read_parquet(a.history)); q=z[z.isin.isin(ISINS)].copy()
    rows=[]
    for isin in ISINS:
        g=q[q.isin==isin]
        if g.empty: rows.append({'isin':isin,'status':'absent'}); continue
        r=one(g); r['status']='ok'; rows.append(r)
    out=pd.DataFrame(rows)
    a.out_dir.mkdir(parents=True,exist_ok=True)
    out.to_csv(a.out_dir/'EXECUTION_GAP_AUDIT_2019_12_31.csv',index=False)
    report={
      'version':'V23_MOMENTUM_EXEC_GAP_AUDIT_1',
      'anchor':str(ANCHOR.date()),
      'purpose':'diagnostic only; future quote information must never be used for signal eligibility',
      'rows':rows,
      'governance':{'strategy_tuning':False,'holdout_2023_2026_accessed':False,'future_quote_fields_audit_only':True}
    }
    (a.out_dir/'EXECUTION_GAP_AUDIT_2019_12_31.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
