from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

from research.retro_5d_portfolio_validation import (
    load_base, load_bench, features, build_trade_paths, trade_table
)
from research.retro_5d_portfolio_ranking_validation import portfolio_ranked
from research.retro_5d_regime_validation import benchmark_regimes

OUT = Path('outputs/retro_5d_ci_run')
OUT.mkdir(parents=True, exist_ok=True)

PATTERN = 'GAP>=7.5 + CLOSE_LOC>=0.8 + RS_RANK>=70 + anti-split + price>=1 + volume>=5000 + ADV20>=800k'
EXIT_CFG = 'DYN_BE_TP20'
RANKING = 'GAP_ASC'
REGIME = 'VOL20_LT25'
MAX_POS = 3
BPS_ONEWAY = 25
INITIAL = 100000.0


def add_mae_mfe(tt: pd.DataFrame) -> pd.DataFrame:
    z = tt.copy()
    maes, mfes = [], []
    for _, r in z.iterrows():
        e = float(r.entry_px)
        lows = np.array([float(r[f'l{k}']) for k in range(1, 6)], dtype=float)
        highs = np.array([float(r[f'h{k}']) for k in range(1, 6)], dtype=float)
        maes.append(100.0 * (np.nanmin(lows) / e - 1.0))
        mfes.append(100.0 * (np.nanmax(highs) / e - 1.0))
    z['mae_pct'] = maes
    z['mfe_pct'] = mfes
    return z


def regime_filter(tt: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    m = market.rename(columns={'date': 'signal_date'}).copy()
    z = tt.merge(m, on='signal_date', how='left')
    return z[z.vol20 < 25].copy()


def metrics(a: pd.DataFrame, final_equity: float, skipped: int, initial: float = INITIAL) -> dict:
    if a.empty:
        return {
            'signals_accepted': 0, 'skipped_capacity': int(skipped), 'final_equity_eur': final_equity,
            'return_pct': 100 * (final_equity / initial - 1), 'wins': 0, 'losses': 0,
            'win_rate_pct': np.nan, 'mean_trade_pct': np.nan, 'median_trade_pct': np.nan,
            'avg_win_pct': np.nan, 'avg_loss_pct': np.nan, 'rr_avg_win_loss': np.nan,
            'profit_factor': np.nan, 'median_mae_pct': np.nan, 'median_mfe_pct': np.nan,
            'max_capacity_pct_adv20': np.nan,
        }
    wins = a[a.net_ret > 0]
    losses = a[a.net_ret < 0]
    gp = float(a.loc[a.pnl_eur > 0, 'pnl_eur'].sum())
    gl = float(-a.loc[a.pnl_eur < 0, 'pnl_eur'].sum())
    avgw = 100 * wins.net_ret.mean() if len(wins) else np.nan
    avgl = 100 * losses.net_ret.mean() if len(losses) else np.nan
    rr = avgw / abs(avgl) if np.isfinite(avgw) and np.isfinite(avgl) and avgl != 0 else np.nan
    return {
        'signals_accepted': int(len(a)), 'skipped_capacity': int(skipped),
        'final_equity_eur': float(final_equity), 'return_pct': float(100 * (final_equity / initial - 1)),
        'wins': int(len(wins)), 'losses': int(len(losses)),
        'win_rate_pct': float(100 * (a.net_ret > 0).mean()),
        'mean_trade_pct': float(100 * a.net_ret.mean()), 'median_trade_pct': float(100 * a.net_ret.median()),
        'avg_win_pct': float(avgw) if np.isfinite(avgw) else np.nan,
        'avg_loss_pct': float(avgl) if np.isfinite(avgl) else np.nan,
        'rr_avg_win_loss': float(rr) if np.isfinite(rr) else np.nan,
        'profit_factor': float(gp / gl) if gl > 0 else np.inf,
        'median_mae_pct': float(a.mae_pct.median()), 'median_mfe_pct': float(a.mfe_pct.median()),
        'max_capacity_pct_adv20': float(a.capacity_pct_adv20.max()),
    }


def realised_drawdown(a: pd.DataFrame, initial: float = INITIAL) -> tuple[float, pd.DataFrame]:
    if a.empty:
        return 0.0, pd.DataFrame(columns=['exit_date', 'pnl_eur', 'equity_eur', 'peak_eur', 'drawdown_pct'])
    q = a[['exit_date', 'pnl_eur']].copy().groupby('exit_date', as_index=False).pnl_eur.sum().sort_values('exit_date')
    q['equity_eur'] = initial + q.pnl_eur.cumsum()
    q['peak_eur'] = q.equity_eur.cummax().clip(lower=initial)
    q['drawdown_pct'] = 100 * (q.equity_eur / q.peak_eur - 1)
    return float(q.drawdown_pct.min()), q


def period_run(tt: pd.DataFrame, lo: int, hi: int, label: str) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    q = tt[(tt.entry_date.dt.year >= lo) & (tt.entry_date.dt.year <= hi)].copy()
    a, final, skipped = portfolio_ranked(q, RANKING, MAX_POS, INITIAL)
    a = add_mae_mfe(a) if len(a) else a
    m = metrics(a, final, skipped)
    dd, eq = realised_drawdown(a)
    m.update({'period': label, 'year_start': lo, 'year_end': hi, 'signals_before_capacity': int(len(q)), 'realised_max_drawdown_pct': dd})
    return m, a, eq


def annual_rows(tt: pd.DataFrame, years) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, trades = [], []
    for y in years:
        q = tt[tt.entry_date.dt.year == y].copy()
        a, final, skipped = portfolio_ranked(q, RANKING, MAX_POS, INITIAL)
        a = add_mae_mfe(a) if len(a) else a
        m = metrics(a, final, skipped)
        dd, _ = realised_drawdown(a)
        m.update({'year': y, 'signals_before_capacity': int(len(q)), 'realised_max_drawdown_pct': dd})
        rows.append(m)
        if len(a): trades.append(a.assign(portfolio_year=y))
    return pd.DataFrame(rows), (pd.concat(trades, ignore_index=True) if trades else pd.DataFrame())


def contribution_table(a: pd.DataFrame) -> pd.DataFrame:
    if a.empty: return pd.DataFrame()
    return (a.groupby('ticker', as_index=False)
             .agg(trades=('ticker','size'), pnl_eur=('pnl_eur','sum'), mean_ret=('net_ret','mean'),
                  wins=('net_ret', lambda s: int((s>0).sum())), mean_mae_pct=('mae_pct','mean'), mean_mfe_pct=('mfe_pct','mean'))
             .assign(mean_ret_pct=lambda d:100*d.mean_ret, win_rate_pct=lambda d:100*d.wins/d.trades)
             .sort_values('pnl_eur', ascending=False))


def ci_status(annual: pd.DataFrame, oos: dict) -> tuple[str, list[str]]:
    reasons = []
    full_years = annual[annual.year.isin([2023, 2024, 2025])]
    positive_full = int((full_years.return_pct > 0).sum())
    above15_full = int((full_years.return_pct >= 15).sum())
    if oos['return_pct'] > 0 and oos['profit_factor'] > 1:
        status = 'WATCH'
        reasons.append('Edge économique positif en OOS agrégé et PF > 1.')
    else:
        status = 'NO-GO'
        reasons.append('Edge économique insuffisant en OOS agrégé.')
    if above15_full == len(full_years) and positive_full == len(full_years):
        status = 'GO_RESEARCH'
        reasons.append('Objectif annuel >=15% atteint sur chaque année complète OOS.')
    else:
        reasons.append(f'Objectif >=15% atteint sur {above15_full}/{len(full_years)} années complètes OOS.')
    reasons.append('2026 est incomplet et ne doit pas être annualisé mécaniquement.')
    reasons.append('2023-2026 a déjà été inspecté : preuve d’évaluation, pas holdout vierge.')
    reasons.append('Biais des survivants historique résiduel : promotion production interdite tant qu’il n’est pas corrigé.')
    return status, reasons


def main():
    raw = load_base()
    feat = features(raw, load_bench(raw.date.min(), raw.date.max()))
    paths = build_trade_paths(feat)
    tt = trade_table(paths, EXIT_CFG, BPS_ONEWAY)
    market = benchmark_regimes(raw.date.min(), raw.date.max())
    filt = regime_filter(tt, market)

    disc, disc_trades, _ = period_run(filt, 2010, 2018, 'DISCOVERY')
    val, val_trades, _ = period_run(filt, 2019, 2022, 'VALIDATION')
    oos, oos_trades, oos_eq = period_run(filt, 2023, 2026, 'OOS_EVALUATION')
    annual, annual_trades = annual_rows(filt, range(2010, 2027))
    contrib = contribution_table(oos_trades)

    status, reasons = ci_status(annual, oos)
    summary = {
        'ci_status': status,
        'pattern': PATTERN,
        'exit': 'SL -10%; après +10% intraday, stop à break-even à partir de la séance suivante; TP +20%; time exit J+5; collision stop/TP = stop first',
        'ranking': RANKING,
        'regime': REGIME,
        'max_positions': MAX_POS,
        'initial_capital_eur': INITIAL,
        'friction_bps_oneway': BPS_ONEWAY,
        'base_rows': int(len(raw)), 'base_tickers': int(raw.ticker.nunique()),
        'date_min': str(raw.date.min().date()), 'date_max': str(raw.date.max().date()),
        'signals_raw_pattern': int(len(paths)), 'signals_after_regime': int(len(filt)),
        'discovery': disc, 'validation': val, 'oos': oos,
        'ci_reasons': reasons,
    }

    pd.DataFrame([disc, val, oos]).to_csv(OUT/'CI_PERIOD_SUMMARY.csv', index=False)
    annual.to_csv(OUT/'CI_ANNUAL_RESULTS_2010_2026.csv', index=False)
    oos_trades.to_csv(OUT/'CI_OOS_TRADES_DETAIL.csv', index=False)
    contrib.to_csv(OUT/'CI_OOS_CONTRIBUTION_BY_TICKER.csv', index=False)
    oos_eq.to_csv(OUT/'CI_OOS_REALISED_EQUITY_DRAWDOWN.csv', index=False)
    if len(annual_trades): annual_trades.to_csv(OUT/'CI_ALL_ANNUAL_TRADES.csv', index=False)
    (OUT/'CI_SUMMARY.json').write_text(json.dumps(summary, indent=2, default=float), encoding='utf-8')

    # Human-readable CI note for direct artifact consumption.
    lines = [
        '# CI — Pattern A Gap Quality / Relative Strength', '',
        f'**Décision CI : {status}**', '',
        f'Base physique : {len(raw):,} lignes / {raw.ticker.nunique():,} tickers / {raw.date.min().date()} au {raw.date.max().date()}.',
        f'Pattern : {PATTERN}.',
        f'Portefeuille : {MAX_POS} positions max, capital initial {INITIAL:,.0f} €, friction {BPS_ONEWAY} pb par sens, ranking {RANKING}, régime {REGIME}.', '',
        '## Résultats par période', '',
        '|Période|Trades|Rendement|Win rate|PF|RR moyen|MDD réalisé|',
        '|---|---:|---:|---:|---:|---:|---:|',
    ]
    for m in [disc, val, oos]:
        lines.append(f"|{m['period']}|{m['signals_accepted']}|{m['return_pct']:.2f}%|{m['win_rate_pct']:.1f}%|{m['profit_factor']:.2f}|{m['rr_avg_win_loss']:.2f}|{m['realised_max_drawdown_pct']:.2f}%|")
    lines += ['', '## Motifs de décision', ''] + [f'- {r}' for r in reasons] + ['', '## Résultats OOS année par année', '',
        '|Année|Trades|Rendement|Win rate|PF|RR|MAE médian|MFE médian|MDD réalisé|',
        '|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for _, r in annual[annual.year >= 2023].iterrows():
        lines.append(f"|{int(r.year)}|{int(r.signals_accepted)}|{r.return_pct:.2f}%|{r.win_rate_pct:.1f}%|{r.profit_factor:.2f}|{r.rr_avg_win_loss:.2f}|{r.median_mae_pct:.2f}%|{r.median_mfe_pct:.2f}%|{r.realised_max_drawdown_pct:.2f}%|")
    lines += ['', '## Gouvernance', '', '- Pattern A gelé : aucune optimisation postérieure sur 2023-2026 dans ce run CI.',
              '- 2023-2026 n’est plus un holdout vierge car cette période a déjà été inspectée lors des recherches précédentes.',
              '- Biais des survivants historique résiduel : résultats utilisables pour recherche/CI, pas pour promotion automatique en production.']
    (OUT/'CI_NOTE.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(summary, indent=2, default=float))


if __name__ == '__main__':
    main()
