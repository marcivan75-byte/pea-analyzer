"""Attribution économique paire des règles anti-FP.

Référence: CONFIRM_J1_ONLY. Pour chaque scénario, compare les trades ayant la même
clé (ticker, signal_date) et sépare les différences de capacité/rotation.
"""
from __future__ import annotations

import argparse, json
from pathlib import Path
import pandas as pd
import numpy as np

SCENARIOS=[
    'CONFIRM_FAIL_FAST','CONFIRM_STRUCTURE_J2','CONFIRM_TRAIL_BE','CONFIRM_MOM_DEAD_J3',
    'CONFIRM_FAIL_FAST_TRAIL','CONFIRM_FAIL_FAST_MOM','FULL_ANTIFP'
]


def _load(path:Path)->pd.DataFrame:
    df=pd.read_csv(path)
    if df.empty: return df
    for c in ['signal_date','entry_date','exit_date']:
        if c in df: df[c]=pd.to_datetime(df[c],errors='coerce',utc=True)
    df['ticker']=df['ticker'].astype(str)
    return df


def _keyed(df:pd.DataFrame)->pd.DataFrame:
    if df.empty: return df
    d=df.copy()
    d['signal_key_date']=d['signal_date'].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    if d.duplicated(['ticker','signal_key_date']).any():
        raise ValueError('BLOCK_ANTIFP_ATTRIBUTION: duplicate trade key')
    return d


def attribute(root:str|Path)->dict:
    root=Path(root)
    ref=_keyed(_load(root/'confirm_j1_only'/'ledger.csv'))
    if ref.empty: raise ValueError('BLOCK_ANTIFP_ATTRIBUTION: empty confirmation reference')
    summaries=[]; detail_parts=[]
    ref_keys=set(zip(ref.ticker,ref.signal_key_date))
    for scenario in SCENARIOS:
        alt=_keyed(_load(root/scenario.lower()/'ledger.csv'))
        alt_keys=set(zip(alt.ticker,alt.signal_key_date))
        merged=ref.merge(alt,on=['ticker','signal_key_date'],suffixes=('_ref','_alt'),how='inner')
        if merged.empty:
            raise ValueError(f'BLOCK_ANTIFP_ATTRIBUTION: no matched trades for {scenario}')
        merged['pnl_delta_eur']=pd.to_numeric(merged['pnl_net_alt'])-pd.to_numeric(merged['pnl_net_ref'])
        merged['return_delta']=pd.to_numeric(merged['return_net_alt'])-pd.to_numeric(merged['return_net_ref'])
        merged['ref_winner']=pd.to_numeric(merged['pnl_net_ref'])>0
        merged['alt_winner']=pd.to_numeric(merged['pnl_net_alt'])>0
        merged['winner_sacrificed']=merged['ref_winner'] & ~merged['alt_winner']
        merged['loser_rescued']=~merged['ref_winner'] & merged['alt_winner']
        merged['loss_reduced']=~merged['ref_winner'] & ~merged['alt_winner'] & (merged['pnl_delta_eur']>0)
        merged['winner_trimmed']=merged['ref_winner'] & merged['alt_winner'] & (merged['pnl_delta_eur']<0)
        merged['scenario']=scenario
        detail_parts.append(merged[['scenario','ticker','signal_key_date','pnl_net_ref','pnl_net_alt','pnl_delta_eur','return_net_ref','return_net_alt','return_delta','exit_reason_ref','exit_reason_alt','winner_sacrificed','loser_rescued','loss_reduced','winner_trimmed']])
        saved=float(merged.loc[merged['pnl_delta_eur']>0,'pnl_delta_eur'].sum())
        forfeited=float(-merged.loc[merged['pnl_delta_eur']<0,'pnl_delta_eur'].sum())
        summaries.append({
            'scenario':scenario,
            'matched_trades':int(len(merged)),
            'reference_only_capacity':int(len(ref_keys-alt_keys)),
            'scenario_only_capacity':int(len(alt_keys-ref_keys)),
            'winner_sacrificed':int(merged['winner_sacrificed'].sum()),
            'loser_rescued':int(merged['loser_rescued'].sum()),
            'loss_reduced':int(merged['loss_reduced'].sum()),
            'winner_trimmed':int(merged['winner_trimmed'].sum()),
            'gross_pnl_saved_eur':saved,
            'gross_pnl_forfeited_eur':forfeited,
            'matched_net_contribution_eur':float(merged['pnl_delta_eur'].sum()),
            'benefit_cost_ratio':None if forfeited==0 else saved/forfeited,
        })
    details=pd.concat(detail_parts,ignore_index=True)
    pd.DataFrame(summaries).to_csv(root/'TABPORT_ANTIFP_PAIRED_ATTRIBUTION.csv',index=False)
    details.to_csv(root/'TABPORT_ANTIFP_PAIRED_DETAIL.csv',index=False)
    out={'status':'PUBLISHED','reference':'CONFIRM_J1_ONLY','summaries':summaries,
         'interpretation':'Positive matched_net_contribution means the rule improved PnL on trades common to both portfolios; capacity effects are reported separately.'}
    (root/'TABPORT_ANTIFP_PAIRED_ATTRIBUTION.json').write_text(json.dumps(out,indent=2,default=str),encoding='utf-8')
    print(json.dumps(out,default=str)); return out


def main():
    p=argparse.ArgumentParser(); p.add_argument('--root',default='outputs/tabport_antifp'); a=p.parse_args(); attribute(a.root)

if __name__=='__main__': main()
