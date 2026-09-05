from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from v182.hebdo.meta_price_history import load_2010_2026

OUT = Path('outputs/retro_5d_factor_decomposition')
OUT.mkdir(parents=True, exist_ok=True)

ROUND_RATIOS = np.array([1.25, 4/3, 1.5, 5/3, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0], dtype=float)


def near_common_split_ratio(r: pd.Series, tol: float = 0.005) -> pd.Series:
    a = pd.to_numeric(r, errors='coerce').to_numpy(float)
    out = np.zeros(len(a), dtype=bool)
    ok = np.isfinite(a) & (a > 0)
    if ok.any():
        vals = a[ok]
        ratios = np.r_[ROUND_RATIOS, 1.0 / ROUND_RATIOS]
        rel = np.abs(vals[:, None] - ratios[None, :]) / ratios[None, :]
        out[ok] = rel.min(axis=1) <= tol
    return pd.Series(out, index=r.index)


def load_base() -> pd.DataFrame:
    df = load_2010_2026(
        'inputs/pre2023/PRE2023_YAHOO_DEVELOPMENT_OHLCV.parquet',
        'inputs/pre2023/PRE2023_YAHOO_CORPUS_MANIFEST.json',
        'data/cache/actions',
    )[['date','ticker','open','high','low','close','volume']].copy()
    df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_localize(None)
    for c in ['open','high','low','close','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['date','ticker','open','high','low','close','volume'])
    df = df[(df.open > 0) & (df.high > 0) & (df.low > 0) & (df.close > 0) & (df.volume >= 0)]
    return df.sort_values(['ticker','date']).drop_duplicates(['ticker','date'], keep='last').reset_index(drop=True)


def load_bench(start, end):
    b = yf.download('^STOXX50E', start=(start-pd.Timedelta(days=200)).strftime('%Y-%m-%d'), end=(end+pd.Timedelta(days=10)).strftime('%Y-%m-%d'), auto_adjust=False, repair=False, progress=False, threads=False)
    if b.empty:
        raise RuntimeError('EURO_STOXX_50_DOWNLOAD_EMPTY')
    if isinstance(b.columns, pd.MultiIndex):
        b.columns = b.columns.get_level_values(0)
    z = pd.DataFrame({'date': pd.to_datetime(b.index).tz_localize(None), 'bench_close': pd.to_numeric(b['Close'], errors='coerce').to_numpy()})
    z = z.dropna().drop_duplicates('date').sort_values('date')
    z['bench_mm20'] = z.bench_close.rolling(20, min_periods=20).mean()
    z['market_ok'] = z.bench_close > z.bench_mm20
    z['bench_ret90'] = z.bench_close.pct_change(90, fill_method=None)
    return z


def feature_engineering(df, bench):
    x = df.copy()
    g = x.groupby('ticker', sort=False, group_keys=False)
    prev = g.close.shift(1)
    x['gap_ratio'] = x.open / prev
    x['gap_pct'] = (x.gap_ratio - 1) * 100
    x['split_ratio_suspect'] = near_common_split_ratio(x.gap_ratio)

    x['adv20_eur'] = g.apply(lambda z: (z['close'] * z['volume']).rolling(20, min_periods=20).mean(), include_groups=False).reset_index(level=0, drop=True).reindex(x.index)
    x['vol_avg20'] = g.volume.transform(lambda s: s.rolling(20, min_periods=20).mean())
    x['rvol20'] = x.volume / x.vol_avg20.replace(0, np.nan)

    x['ret90'] = g.close.pct_change(90, fill_method=None)
    mean20 = g.close.transform(lambda s: s.rolling(20, min_periods=20).mean())
    std20 = g.close.transform(lambda s: s.rolling(20, min_periods=20).std())
    x['volatility20'] = std20 / mean20.replace(0, np.nan)
    x['volatility20_avg20'] = x.groupby('ticker', sort=False).volatility20.transform(lambda s: s.rolling(20, min_periods=20).mean())
    x['base_lowvol'] = x.volatility20 < x.volatility20_avg20

    gg = x.groupby('ticker', sort=False, group_keys=False)
    x['close_j5'] = gg.close.shift(-5)
    x['open_j1'] = gg.open.shift(-1)
    x['close_j1'] = gg.close.shift(-1)
    x['target_close_j_to_j5_pct'] = (x.close_j5 / x.close - 1) * 100
    x['target_pit_open_j1_to_close_j5_pct'] = (x.close_j5 / x.open_j1 - 1) * 100

    x = x.merge(bench[['date','bench_ret90','market_ok']], on='date', how='left')
    x['rs'] = (1 + x.ret90) / (1 + x.bench_ret90) - 1
    x['rs_rank'] = x.groupby('date').rs.rank(pct=True, method='average') * 100
    x['year'] = x.date.dt.year
    x['period'] = np.select([x.year <= 2018, x.year <= 2022], ['DISC_2010_2018','VAL_2019_2022'], default='OOS_2023_2026')
    return x


def eligible_mask(x, adv_floor=800_000):
    return (
        x.close.between(1, 10000) &
        x.open.between(1, 10000) &
        (x.volume >= 5000) &
        (x.adv20_eur >= adv_floor) &
        (~x.split_ratio_suspect) &
        x.target_close_j_to_j5_pct.notna() &
        x.target_pit_open_j1_to_close_j5_pct.notna() &
        x.target_close_j_to_j5_pct.between(-80, 80) &
        x.target_pit_open_j1_to_close_j5_pct.between(-80, 80)
    )


def metric_row(x, mask, label, period, target_col, base_rate):
    z = x.loc[mask & (x.period == period), target_col].dropna()
    n = int(len(z)); wins = int((z >= 20).sum())
    p = 100 * wins / n if n else np.nan
    return {
        'pattern': label, 'period': period, 'target': target_col, 'signals': n, 'wins_ge20': wins,
        'precision_pct': p, 'base_rate_pct': base_rate, 'lift_vs_base': (p/base_rate if n and base_rate > 0 else np.nan),
        'mean_fwd_pct': float(z.mean()) if n else np.nan, 'median_fwd_pct': float(z.median()) if n else np.nan,
    }


def evaluate_patterns(x, elig):
    factors = {
        'GAP5': x.gap_pct >= 5,
        'GAP10': x.gap_pct >= 10,
        'GAP15': x.gap_pct >= 15,
        'GAP20': x.gap_pct >= 20,
        'RVOL2': x.rvol20 >= 2,
        'RVOL3': x.rvol20 >= 3,
        'RVOL5': x.rvol20 >= 5,
        'RVOL8': x.rvol20 >= 8,
        'RS50': x.rs_rank >= 50,
        'RS60': x.rs_rank >= 60,
        'RS70': x.rs_rank >= 70,
        'RS80': x.rs_rank >= 80,
        'RS90': x.rs_rank >= 90,
        'BASE': x.base_lowvol.fillna(False),
        'MKT': x.market_ok.fillna(False),
    }
    periods = ['DISC_2010_2018','VAL_2019_2022','OOS_2023_2026']
    targets = ['target_close_j_to_j5_pct','target_pit_open_j1_to_close_j5_pct']
    rows = []
    for target in targets:
        base_rates = {}
        for p in periods:
            z = x.loc[elig & (x.period == p), target].dropna()
            base_rates[p] = 100 * (z >= 20).mean() if len(z) else np.nan
            rows.append(metric_row(x, elig, 'ELIGIBLE_BASE', p, target, base_rates[p]))
        for name, m in factors.items():
            for p in periods:
                rows.append(metric_row(x, elig & m, name, p, target, base_rates[p]))

        # Progressive combinations around the user's original hypothesis.
        combo_defs = {
            'GAP20+RVOL8': factors['GAP20'] & factors['RVOL8'],
            'GAP20+RS70': factors['GAP20'] & factors['RS70'],
            'RVOL8+RS70': factors['RVOL8'] & factors['RS70'],
            'GAP20+RVOL8+RS70': factors['GAP20'] & factors['RVOL8'] & factors['RS70'],
            'GAP20+RVOL8+BASE': factors['GAP20'] & factors['RVOL8'] & factors['BASE'],
            'GAP20+RVOL8+MKT': factors['GAP20'] & factors['RVOL8'] & factors['MKT'],
            'GAP20+RVOL8+RS70+BASE': factors['GAP20'] & factors['RVOL8'] & factors['RS70'] & factors['BASE'],
            'GAP20+RVOL8+RS70+MKT': factors['GAP20'] & factors['RVOL8'] & factors['RS70'] & factors['MKT'],
            'ORIGINAL5': factors['GAP20'] & factors['RVOL8'] & factors['RS70'] & factors['BASE'] & factors['MKT'],
        }
        for name, m in combo_defs.items():
            for p in periods:
                rows.append(metric_row(x, elig & m, name, p, target, base_rates[p]))
    return pd.DataFrame(rows)


def liquidity_sensitivity(x):
    rows = []
    for adv in [250_000, 500_000, 800_000, 1_000_000, 2_000_000]:
        elig = eligible_mask(x, adv)
        base = elig
        original = elig & (x.gap_pct >= 20) & (x.rvol20 >= 8) & (x.rs_rank >= 70) & x.base_lowvol.fillna(False) & x.market_ok.fillna(False)
        for label, m in [('BASE',base),('ORIGINAL5',original)]:
            for p in ['DISC_2010_2018','VAL_2019_2022','OOS_2023_2026']:
                z=x.loc[m & (x.period==p),'target_pit_open_j1_to_close_j5_pct'].dropna()
                rows.append({'adv_floor_eur':adv,'pattern':label,'period':p,'signals':len(z),'wins_ge20':int((z>=20).sum()),'precision_pct':100*(z>=20).mean() if len(z) else np.nan,'mean_return_pct':z.mean() if len(z) else np.nan})
    return pd.DataFrame(rows)


def top_oos_candidates(stats):
    p = stats[(stats.target=='target_pit_open_j1_to_close_j5_pct') & (stats.period=='OOS_2023_2026') & (stats.pattern!='ELIGIBLE_BASE')].copy()
    return p.sort_values(['lift_vs_base','precision_pct','signals'], ascending=[False,False,False])


def main():
    raw=load_base(); bench=load_bench(raw.date.min(), raw.date.max()); x=feature_engineering(raw, bench)
    elig=eligible_mask(x, 800_000)
    stats=evaluate_patterns(x, elig)
    stats.to_csv(OUT/'FACTOR_STATS_DISC_VAL_OOS.csv', index=False)
    liquidity_sensitivity(x).to_csv(OUT/'LIQUIDITY_SENSITIVITY.csv', index=False)
    top=top_oos_candidates(stats)
    top.to_csv(OUT/'OOS_FACTOR_RANKING.csv', index=False)

    # Winners and non-winners for diagnostic comparison.
    cols=['date','ticker','close','open','volume','adv20_eur','gap_pct','rvol20','rs_rank','base_lowvol','market_ok','target_close_j_to_j5_pct','target_pit_open_j1_to_close_j5_pct','period']
    q=x.loc[elig,cols].copy()
    q['winner20_pit'] = q.target_pit_open_j1_to_close_j5_pct >= 20
    q[q.winner20_pit].to_csv(OUT/'WINNERS_GE20_PIT.csv', index=False)

    summary={
        'rows_base':int(len(raw)), 'tickers':int(raw.ticker.nunique()), 'eligible_adv800k':int(elig.sum()),
        'excluded_split_ratio_suspect':int(x.split_ratio_suspect.sum()),
        'period_counts':q.period.value_counts().to_dict(),
        'winner_counts_pit':q.groupby('period').winner20_pit.sum().astype(int).to_dict(),
        'method':'Fixed thresholds; discovery 2010-2018, validation 2019-2022, OOS 2023-2026; OOS not used to set thresholds.',
        'liquidity':'close/open >=1 EUR, volume >=5000, ADV20 >=800k EUR baseline; sensitivity 250k-2m.',
        'ost_proxy':'exclude opening gap ratios within 0.5% of common split ratios and reciprocal ratios; still research-grade, not issuer-action certified.'
    }
    (OUT/'SUMMARY.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    print('\nTOP OOS PIT\n', top.head(25).to_string(index=False))

if __name__=='__main__':
    main()
