from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

CUTOFF = pd.Timestamp('2023-01-01')
AUDIT_START = '2018-01-01'
MIN_OVERLAP = 400
MIN_CORR = 0.9999
MAX_RATIO_DEVIATION = 0.001
MAX_REL_MAD = 0.001
MIN_PATCH_ROWS = 100

# Whitelist frozen from diagnosed execution gaps only. Adding a security here is a
# data-quality repair action, not a strategy-selection action. Every patch must pass
# exact/near-exact overlap identity gates before any missing history is appended.
MAP = {
    'NO0010708068': {'name': 'Vow ASA', 'ticker': 'VOW.OL'},
    'NO0010598683': {'name': 'Hofseth BioCare ASA', 'ticker': 'HBC.OL'},
    'IT0001469995': {'name': 'Digital Bros S.p.A.', 'ticker': 'DIB.MI'},
    'NO0010159684': {'name': 'Medistim ASA', 'ticker': 'MEDI.OL'},
    'NL0000334118': {'name': 'ASM International N.V.', 'ticker': 'ASM.AS'},
    'DK0060520450': {'name': 'Napatech A/S', 'ticker': 'NAPA.OL'},
    'FR0011716265': {'name': 'Crossject SA', 'ticker': 'ALCJ.PA'},
    'IT0003895668': {'name': 'Eurotech S.p.A.', 'ticker': 'ETH.MI'},
    'NO0010205966': {'name': 'Navamedic ASA', 'ticker': 'NAVA.OL'},
}


def norm(df: pd.DataFrame) -> pd.DataFrame:
    m={str(c).strip().lower():c for c in df.columns}
    def pick(*xs):
        for x in xs:
            if x in m:return m[x]
        raise SystemExit(f'BLOCK_SCHEMA {xs}')
    z=pd.DataFrame({
        'isin':df[pick('isin')].astype(str),
        'date':pd.to_datetime(df[pick('date','datetime')],errors='coerce').dt.tz_localize(None).dt.normalize(),
        'close':pd.to_numeric(df[pick('close','adj_close','adjusted_close')],errors='coerce')})
    z=z.dropna().query('close>0').copy()
    z=z[z['date']<CUTOFF].drop_duplicates(['isin','date'],keep='last').sort_values(['isin','date'])
    return z


def source(ticker:str)->pd.DataFrame:
    x=yf.download(ticker,start=AUDIT_START,end='2023-01-01',auto_adjust=False,actions=True,progress=False,threads=False)
    if x is None or x.empty: raise SystemExit(f'BLOCK_SOURCE_EMPTY {ticker}')
    if isinstance(x.columns,pd.MultiIndex): x.columns=[c[0] for c in x.columns]
    x=x.reset_index(); dcol='Date' if 'Date' in x.columns else x.columns[0]
    s=pd.DataFrame({'date':pd.to_datetime(x[dcol],errors='coerce').dt.tz_localize(None).dt.normalize(),
                    'source_close':pd.to_numeric(x['Close'],errors='coerce')}).dropna()
    return s[(s.source_close>0)&(s.date<CUTOFF)].sort_values('date')


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--history',type=Path,required=True); ap.add_argument('--out-dir',type=Path,required=True); a=ap.parse_args()
    z=norm(pd.read_parquet(a.history)); patches=[]; prov=[]
    for isin,meta in MAP.items():
        h=z[z['isin'].eq(isin)][['date','close']].sort_values('date').copy()
        if h.empty: raise SystemExit(f'BLOCK_MASTER_EMPTY {isin}')
        s=source(meta['ticker'])
        o=h.merge(s,on='date',how='inner')
        if len(o)<MIN_OVERLAP: raise SystemExit(f'BLOCK_OVERLAP {isin} n={len(o)}')
        ratio=o['close']/o['source_close']; med=float(ratio.median()); mad=float((ratio-med).abs().median()); corr=float(o[['close','source_close']].corr().iloc[0,1])
        rel_mad=mad/max(abs(med),1e-12)
        if corr<MIN_CORR or abs(med-1.0)>MAX_RATIO_DEVIATION or rel_mad>MAX_REL_MAD:
            raise SystemExit(f'BLOCK_IDENTITY {isin} corr={corr} ratio={med} rel_mad={rel_mad}')
        last=h['date'].max(); p=s[s['date']>last][['date','source_close']].copy()
        if len(p)<MIN_PATCH_ROWS: raise SystemExit(f'BLOCK_PATCH_ROWS {isin} n={len(p)}')
        p['isin']=isin; p['close']=p['source_close']*med; p=p[['isin','date','close']]
        patches.append(p)
        prov.append({'isin':isin,'name':meta['name'],'ticker':meta['ticker'],'master_last_date':str(last.date()),'patch_rows':int(len(p)),'patch_first_date':str(p.date.min().date()),'patch_last_date':str(p.date.max().date()),'overlap_rows':int(len(o)),'corr':corr,'median_ratio':med,'relative_mad':rel_mad,'status':'PATCH_ACCEPTED'})
    patch=pd.concat(patches,ignore_index=True)
    if patch.duplicated(['isin','date']).any(): raise SystemExit('BLOCK_PATCH_DUPLICATES')
    overlay=pd.concat([z,patch],ignore_index=True).drop_duplicates(['isin','date'],keep='first').sort_values(['isin','date']).reset_index(drop=True)
    if overlay['date'].max()>=CUTOFF: raise SystemExit('BLOCK_CUTOFF')
    a.out_dir.mkdir(parents=True,exist_ok=True)
    overlay.to_parquet(a.out_dir/'PEA_CLOSE_PRE2023_REPAIRED_OVERLAY.parquet',index=False)
    patch.to_csv(a.out_dir/'REPAIR_PATCH_ROWS.csv',index=False)
    pd.DataFrame(prov).to_csv(a.out_dir/'REPAIR_PROVENANCE.csv',index=False)
    report={'version':'V23_PRE2023_HISTORY_REPAIR_OVERLAY_2','canonical_master_mutated':False,'strategy_tuning':False,'holdout_2023_2026_accessed':False,'survivorship_bias_remains':True,'patched_isins':len(prov),'patch_rows_total':int(len(patch)),'quality_gates':{'min_overlap':MIN_OVERLAP,'min_corr':MIN_CORR,'max_ratio_deviation':MAX_RATIO_DEVIATION,'max_relative_mad':MAX_REL_MAD,'min_patch_rows':MIN_PATCH_ROWS},'provenance':prov}
    (a.out_dir/'REPAIR_REPORT.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
