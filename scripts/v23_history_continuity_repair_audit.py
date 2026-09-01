from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

CUTOFF = pd.Timestamp('2023-01-01')
AUDIT_START = '2018-01-01'
AUDIT_END = '2023-01-01'

# Frozen before this audit from exchange/company identity checks. This is a data-repair
# diagnostic only; the mappings are not strategy parameters and no 2023+ strategy result
# is consulted.
MAP = {
    'NO0010708068': {'name': 'Vow ASA', 'ticker': 'VOW.OL'},
    'NO0010598683': {'name': 'Hofseth BioCare ASA', 'ticker': 'HBC.OL'},
    'IT0001469995': {'name': 'Digital Bros S.p.A.', 'ticker': 'DIB.MI'},
    'NO0010159684': {'name': 'Medistim ASA', 'ticker': 'MEDI.OL'},
    'NL0000334118': {'name': 'ASM International N.V.', 'ticker': 'ASM.AS'},
}


def norm_history(df: pd.DataFrame) -> pd.DataFrame:
    m = {str(c).strip().lower(): c for c in df.columns}
    def pick(*xs):
        for x in xs:
            if x in m:
                return m[x]
        raise SystemExit(f'BLOCK_SCHEMA missing {xs}')
    z = pd.DataFrame({
        'isin': df[pick('isin')].astype(str),
        'date': pd.to_datetime(df[pick('date','datetime')], errors='coerce').dt.tz_localize(None).dt.normalize(),
        'close': pd.to_numeric(df[pick('close','adj_close','adjusted_close')], errors='coerce'),
    })
    z = z.dropna().query('close > 0').copy()
    z = z[z['date'] < CUTOFF].drop_duplicates(['isin','date'], keep='last').sort_values(['isin','date'])
    return z


def fetch(ticker: str) -> pd.DataFrame:
    x = yf.download(ticker, start=AUDIT_START, end=AUDIT_END, auto_adjust=False, actions=True,
                    progress=False, threads=False)
    if x is None or x.empty:
        return pd.DataFrame(columns=['date','source_close','source_adj_close'])
    if isinstance(x.columns, pd.MultiIndex):
        x.columns = [c[0] for c in x.columns]
    x = x.reset_index()
    dcol = 'Date' if 'Date' in x.columns else x.columns[0]
    out = pd.DataFrame({
        'date': pd.to_datetime(x[dcol], errors='coerce').dt.tz_localize(None).dt.normalize(),
        'source_close': pd.to_numeric(x.get('Close'), errors='coerce'),
        'source_adj_close': pd.to_numeric(x.get('Adj Close'), errors='coerce') if 'Adj Close' in x else np.nan,
    }).dropna(subset=['date','source_close'])
    return out[out['date'] < CUTOFF].sort_values('date')


def audit_one(z: pd.DataFrame, isin: str, meta: dict) -> tuple[dict, pd.DataFrame]:
    h = z[z['isin'].eq(isin)][['date','close']].copy().sort_values('date')
    s = fetch(meta['ticker'])
    merged = h.merge(s, on='date', how='outer', indicator=True).sort_values('date')
    overlap = merged[merged['_merge'].eq('both') & merged['close'].notna() & merged['source_close'].notna()].copy()
    if len(overlap):
        ratio = overlap['close'] / overlap['source_close']
        med_ratio = float(ratio.median())
        mad_ratio = float((ratio - ratio.median()).abs().median())
        corr = float(overlap[['close','source_close']].corr().iloc[0,1]) if len(overlap) > 1 else None
    else:
        med_ratio = mad_ratio = corr = None

    h_last = h['date'].max() if len(h) else pd.NaT
    s_last = s['date'].max() if len(s) else pd.NaT
    source_after_hist = int((s['date'] > h_last).sum()) if pd.notna(h_last) else int(len(s))
    source_2020_2022 = int(((s['date'] >= '2020-01-01') & (s['date'] < CUTOFF)).sum())
    hist_2020_2022 = int(((h['date'] >= '2020-01-01') & (h['date'] < CUTOFF)).sum())
    source_continues = bool(source_2020_2022 >= 100)

    result = {
        'isin': isin,
        'name': meta['name'],
        'source_ticker': meta['ticker'],
        'history_rows_2018_2022': int(len(h)),
        'source_rows_2018_2022': int(len(s)),
        'history_last_date': str(h_last.date()) if pd.notna(h_last) else None,
        'source_last_date_pre2023': str(s_last.date()) if pd.notna(s_last) else None,
        'history_rows_2020_2022': hist_2020_2022,
        'source_rows_2020_2022': source_2020_2022,
        'source_rows_after_history_last_date': source_after_hist,
        'overlap_rows': int(len(overlap)),
        'median_history_to_source_close_ratio': med_ratio,
        'mad_history_to_source_close_ratio': mad_ratio,
        'overlap_close_correlation': corr,
        'independent_source_continues_2020_2022': source_continues,
        'classification': 'HISTORY_CONTINUITY_BREAK_CONFIRMED' if source_continues and hist_2020_2022 < 20 else 'REVIEW',
    }
    merged.insert(0, 'isin', isin)
    merged.insert(1, 'source_ticker', meta['ticker'])
    return result, merged


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--history', type=Path, required=True)
    ap.add_argument('--out-dir', type=Path, required=True)
    a = ap.parse_args()

    z = norm_history(pd.read_parquet(a.history))
    a.out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    details = []
    for isin, meta in MAP.items():
        r, d = audit_one(z, isin, meta)
        rows.append(r); details.append(d)
    summary = pd.DataFrame(rows)
    detail = pd.concat(details, ignore_index=True) if details else pd.DataFrame()
    confirmed = int(summary['classification'].eq('HISTORY_CONTINUITY_BREAK_CONFIRMED').sum())
    report = {
        'version': 'V23_HISTORY_CONTINUITY_REPAIR_AUDIT_1',
        'purpose': 'diagnose the five non-terminal 2019 execution gaps against independent public ticker histories',
        'governance': {
            'strategy_tuning': False,
            'holdout_2023_2026_accessed_for_strategy': False,
            'source_window_end_exclusive': AUDIT_END,
            'mapping_frozen_before_download': True,
            'survivorship_bias_in_master_history': True,
        },
        'audited_isins': int(len(summary)),
        'continuity_breaks_confirmed': confirmed,
        'all_five_confirmed': bool(confirmed == 5),
        'results': rows,
        'decision_rule': 'If independent sources continue through 2020-2022 while master history stops in 2019, repair or quarantine those histories before any stock-picking CAGR is promotable.',
    }
    summary.to_csv(a.out_dir/'HISTORY_CONTINUITY_AUDIT_SUMMARY.csv', index=False)
    detail.to_csv(a.out_dir/'HISTORY_CONTINUITY_AUDIT_DETAIL.csv', index=False)
    (a.out_dir/'HISTORY_CONTINUITY_AUDIT_REPORT.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
