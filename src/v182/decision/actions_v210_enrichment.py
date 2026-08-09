from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3] if len(Path(__file__).resolve().parents) > 3 else Path.cwd()
CONFIG = Path('data/reference/V21.0_ACTIONS_PEA_CONFIG.json')
TARGET = Path('outputs/V21.0_ACTIONS_PEA_1429_PREPARED.csv')
AUDIT = Path('outputs/audit/V21.0_ACTIONS_DIRECT_ENRICHMENT.json')


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors='coerce')


def _pct(v):
    try:
        x = float(v)
        if not math.isfinite(x):
            return None
        return x * 100.0 if abs(x) <= 1.5 else x
    except Exception:
        return None


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _price_ratio(canonical, observed):
    c, o = _f(canonical), _f(observed)
    if c is None or o is None or c <= 0 or o <= 0:
        return None
    return o / c


def _price_match(canonical, observed, tolerance=0.25):
    ratio = _price_ratio(canonical, observed)
    return (ratio is not None and (1.0 - tolerance) <= ratio <= (1.0 + tolerance)), ratio


def _history_metrics(h: pd.DataFrame) -> dict:
    if h is None or h.empty:
        return {}
    close = pd.to_numeric(h.get('Close'), errors='coerce').dropna()
    high = pd.to_numeric(h.get('High'), errors='coerce') if 'High' in h else close
    low = pd.to_numeric(h.get('Low'), errors='coerce') if 'Low' in h else close
    vol = pd.to_numeric(h.get('Volume'), errors='coerce') if 'Volume' in h else pd.Series(index=h.index, dtype=float)
    if len(close) < 25:
        return {}
    c = float(close.iloc[-1])
    h52 = float(high.tail(252).max()) if len(high) else None
    l52 = float(low.tail(252).min()) if len(low) else None
    ret = close.pct_change().dropna().tail(252)
    vol1 = float(ret.std(ddof=0) * math.sqrt(252) * 100.0) if len(ret) >= 20 else None
    ll = low.rolling(14).min()
    hh = high.rolling(14).max()
    k = (pd.to_numeric(h.get('Close'), errors='coerce') - ll) / (hh - ll).replace(0, np.nan) * 100.0
    d = k.rolling(3).mean()
    k_now = _f(k.iloc[-1]) if len(k) else None
    d_now = _f(d.iloc[-1]) if len(d) else None
    bull = bear = False
    if len(k) >= 2 and len(d) >= 2 and pd.notna(k.iloc[-2]) and pd.notna(d.iloc[-2]) and k_now is not None and d_now is not None:
        bull = bool(k.iloc[-2] <= d.iloc[-2] and k_now > d_now)
        bear = bool(k.iloc[-2] >= d.iloc[-2] and k_now < d_now)
    vavg = _f(vol.tail(20).mean()) if len(vol.dropna()) else None
    vacc = _f(vol.dropna().iloc[-1] / vavg) if vavg and vavg > 0 and len(vol.dropna()) else None
    prior20 = close.shift(1).rolling(20).max()
    breakout = bool(pd.notna(prior20.iloc[-1]) and c > float(prior20.iloc[-1]))
    return {
        '_validation_close': c,
        'high_52w': h52,
        'low_52w': l52,
        'distance_to_52w_high_pct': ((c / h52) - 1) * 100 if h52 else None,
        'distance_to_52w_low_pct': ((c / l52) - 1) * 100 if l52 else None,
        'stoch_k': k_now,
        'stoch_d': d_now,
        'stoch_bull_cross_flag': bull,
        'stoch_bear_cross_flag': bear,
        'volume_avg_20d': vavg,
        'volume_acceleration_20d': vacc,
        'breakout_20d_flag': breakout,
        'volatility_1y_pct': vol1,
    }


def apply(root: Path | None = None) -> dict:
    root = root or ROOT
    cfg = json.loads((root / CONFIG).read_text(encoding='utf-8'))
    path = root / TARGET
    df = pd.read_csv(path, sep=';', dtype=object, encoding='utf-8-sig', low_memory=False)
    if len(df) != int(cfg['canonical_universe_size']):
        raise RuntimeError('V21 enrichment universe gate')

    priority = df['v210_enrichment_priority'].astype(str).str.lower().isin({'true', '1', 'yes'})
    tickers = df.loc[priority, 'yahoo_ticker'].dropna().astype(str).str.strip()
    tickers = [x for x in tickers.unique().tolist() if x and x.lower() != 'nan']

    # Explicit identity provenance for every row. Direct Yahoo data is never trusted solely by symbol.
    df['direct_yahoo_identity_status'] = np.where(priority, 'PENDING_VALIDATION', 'NOT_PRIORITIZED')
    df['direct_yahoo_price_ratio'] = np.nan
    df['direct_yahoo_history_applied'] = False
    df['direct_yahoo_info_applied'] = False

    history_success = 0
    info_success = 0
    history_mismatches = 0
    info_mismatches = 0
    info_failures = []

    try:
        import yfinance as yf

        for start in range(0, len(tickers), 75):
            batch = tickers[start:start + 75]
            try:
                hist = yf.download(batch, period='5y', interval='1d', group_by='ticker', auto_adjust=False, actions=False, threads=True, progress=False, timeout=25)
            except Exception:
                continue
            for ticker in batch:
                try:
                    h = hist[ticker] if isinstance(hist.columns, pd.MultiIndex) and ticker in hist.columns.get_level_values(0) else hist
                    metrics = _history_metrics(h)
                    if not metrics:
                        continue
                    idx = df.index[df['yahoo_ticker'].astype(str).eq(ticker)]
                    if len(idx) == 0:
                        continue
                    canonical = _f(_num(df.loc[idx], 'last_close').dropna().median())
                    matched, ratio = _price_match(canonical, metrics.pop('_validation_close', None))
                    if ratio is not None:
                        df.loc[idx, 'direct_yahoo_price_ratio'] = ratio
                    if canonical is not None and not matched:
                        df.loc[idx, 'direct_yahoo_identity_status'] = 'HISTORY_PRICE_MISMATCH_REJECTED'
                        history_mismatches += 1
                        continue
                    for field, val in metrics.items():
                        df.loc[idx, field] = val
                    df.loc[idx, 'direct_yahoo_history_applied'] = True
                    df.loc[idx, 'direct_yahoo_identity_status'] = 'HISTORY_PRICE_MATCH' if matched else 'HISTORY_PRICE_NOT_AVAILABLE'
                    history_success += 1
                except Exception:
                    continue

        consecutive_rate = 0
        mappings = {
            'marketCap': 'market_cap_v21', 'enterpriseValue': 'enterprise_value_v21', 'forwardPE': 'per_forward_v21', 'trailingPE': 'per_ttm_v21',
            'priceToBook': 'pb_v21', 'freeCashflow': 'free_cash_flow_v21', 'operatingCashflow': 'operating_cash_flow_v21',
            'totalDebt': 'total_debt_v21', 'totalCash': 'total_cash_v21', 'ebitda': 'ebitda_v21', 'debtToEquity': 'debt_to_equity_v21',
            'currentRatio': 'current_ratio_v21', 'beta': 'beta_v21', 'targetMeanPrice': 'target_mean_v21', 'targetLowPrice': 'target_low_v21',
            'targetHighPrice': 'target_high_v21', 'numberOfAnalystOpinions': 'n_analysts_v21', 'shortRatio': 'short_ratio',
        }
        pct_maps = {
            'returnOnEquity': 'roe_v21_pct', 'returnOnAssets': 'roa_v21_pct', 'operatingMargins': 'operating_margin_v21_pct',
            'profitMargins': 'net_margin_v21_pct', 'grossMargins': 'gross_margin_v21_pct', 'revenueGrowth': 'revenue_growth_v21_pct',
            'earningsGrowth': 'earnings_growth_v21_pct', 'dividendYield': 'dividend_yield_v21_pct', 'payoutRatio': 'payout_ratio_v21_pct',
            'heldPercentInstitutions': 'institutional_ownership_pct', 'heldPercentInsiders': 'insider_ownership_pct', 'shortPercentOfFloat': 'short_percent_float_pct',
        }
        for ticker in tickers:
            try:
                info = yf.Ticker(ticker).get_info() or {}
                idx = df.index[df['yahoo_ticker'].astype(str).eq(ticker)]
                if len(idx) == 0:
                    continue
                canonical = _f(_num(df.loc[idx], 'last_close').dropna().median())
                observed = _f(info.get('currentPrice')) or _f(info.get('regularMarketPrice')) or _f(info.get('previousClose'))
                matched, ratio = _price_match(canonical, observed)
                if ratio is not None:
                    df.loc[idx, 'direct_yahoo_price_ratio'] = ratio
                # If Yahoo gives a price, it must match the canonical instrument. If it gives no price,
                # a qualified European ticker is allowed only when the history path already matched.
                qualified = '.' in ticker
                history_ok = df.loc[idx, 'direct_yahoo_history_applied'].astype(bool).any()
                allow = matched or (observed is None and qualified and history_ok)
                if canonical is not None and not allow:
                    df.loc[idx, 'direct_yahoo_identity_status'] = 'INFO_PRICE_MISMATCH_REJECTED' if observed is not None else 'INFO_IDENTITY_UNVERIFIED_REJECTED'
                    info_mismatches += 1
                    info_failures.append({'ticker': ticker, 'error': 'IDENTITY_PRICE_MISMATCH_OR_UNVERIFIED', 'canonical': canonical, 'observed': observed, 'ratio': ratio})
                    continue

                for src, dst in mappings.items():
                    val = _f(info.get(src))
                    if val is not None:
                        df.loc[idx, dst] = val
                for src, dst in pct_maps.items():
                    val = _pct(info.get(src))
                    if val is not None:
                        df.loc[idx, dst] = val
                ts = info.get('earningsTimestamp') or info.get('earningsTimestampStart')
                if ts:
                    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                    df.loc[idx, 'next_earnings_date'] = dt.date().isoformat()
                    days = (dt.date() - datetime.now(timezone.utc).date()).days
                    df.loc[idx, 'days_to_earnings'] = days
                    df.loc[idx, 'earnings_window_7d_flag'] = bool(0 <= days <= 7)
                    df.loc[idx, 'earnings_window_30d_flag'] = bool(0 <= days <= 30)
                rec = info.get('recommendationMean')
                if rec is not None:
                    score = (5.0 - float(rec)) / 4.0 * 100.0
                    df.loc[idx, 'consensus_score_100_v21'] = max(0, min(100, score))
                mc, fcf, debt, ebitda = _f(info.get('marketCap')), _f(info.get('freeCashflow')), _f(info.get('totalDebt')), _f(info.get('ebitda'))
                if mc and fcf is not None and mc != 0:
                    df.loc[idx, 'fcf_yield_v21'] = fcf / mc * 100.0
                if debt is not None and ebitda and ebitda != 0:
                    df.loc[idx, 'debt_to_ebitda_v21'] = debt / ebitda
                df.loc[idx, 'direct_yahoo_info_applied'] = True
                df.loc[idx, 'direct_yahoo_identity_status'] = 'INFO_PRICE_MATCH' if matched else 'INFO_ACCEPTED_AFTER_HISTORY_MATCH'
                info_success += 1
                consecutive_rate = 0
                time.sleep(.12)
            except Exception as exc:
                text = f'{type(exc).__name__}: {exc}'
                info_failures.append({'ticker': ticker, 'error': text[:180]})
                if any(x in text.lower() for x in ['429', 'rate limit', 'too many requests']):
                    consecutive_rate += 1
                    if consecutive_rate >= 3:
                        break
                else:
                    consecutive_rate = 0
    except Exception as exc:
        info_failures.append({'ticker': '__SETUP__', 'error': f'{type(exc).__name__}: {str(exc)[:180]}'})

    # Safe coalescing to inherited/canonical fields after guarded direct collection.
    for target, sources in {
        'market_cap': ['market_cap_v21', 'market_cap'],
        'per_forward_v21': ['per_forward_v21', 'per_forward', 'per_forward_yf'],
        'per_ttm_v21': ['per_ttm_v21', 'per_ttm', 'per_ttm_yf'],
        'pb_v21': ['pb_v21', 'pb'],
        'beta_v21': ['beta_v21', 'beta'],
    }.items():
        out = pd.Series(np.nan, index=df.index, dtype=float)
        for src in sources:
            if src in df:
                out = out.where(out.notna(), _num(df, src))
        df[target] = out

    last = _num(df, 'last_close')
    target = _num(df, 'target_mean_v21')
    df['target_upside_pct_v21'] = ((target / last) - 1) * 100
    df.loc[last.le(0) | last.isna() | target.isna(), 'target_upside_pct_v21'] = np.nan
    df['potential_gt_15_flag'] = df['target_upside_pct_v21'].ge(15).where(df['target_upside_pct_v21'].notna())

    am, cs, eps = _num(df, 'analyst_momentum_score'), _num(df, 'consensus_score_100_v21'), _num(df, 'eps_revision_3m')
    parts = []
    if am.notna().any(): parts.append((am, .45))
    if cs.notna().any(): parts.append((cs, .30))
    if eps.notna().any(): parts.append(((50 + eps * 5).clip(0, 100), .25))
    if parts:
        numerator = sum(s.fillna(0) * w for s, w in parts)
        denominator = sum(s.notna().astype(float) * w for s, w in parts)
        catalyst = numerator / denominator.replace(0, np.nan)
        days = _num(df, 'days_to_earnings')
        df['earnings_catalyst_score'] = catalyst.where(days.between(0, 30))
    days = _num(df, 'days_to_earnings')
    df['earnings_risk_gate'] = np.where(days.between(0, int(cfg['gates']['earnings_imminent_days'])), 'IMMINENT_REVIEW', 'PASS')
    df.loc[days.isna(), 'earnings_risk_gate'] = 'DATA_NOT_AVAILABLE'

    df.to_csv(path, sep=';', index=False, encoding='utf-8-sig')
    coverage = {f: round(float(df[f].notna().mean()), 4) for f in ['high_52w', 'stoch_k', 'volatility_1y_pct', 'market_cap_v21', 'per_forward_v21', 'roe_v21_pct', 'target_mean_v21', 'next_earnings_date'] if f in df}
    audit = {
        'passed': True, 'rows': len(df), 'priority_tickers': len(tickers), 'history_success': history_success, 'info_success': info_success,
        'history_identity_rejections': history_mismatches, 'info_identity_rejections': info_mismatches,
        'info_failures': len(info_failures), 'coverage': coverage, 'failures_sample': info_failures[:20],
        'identity_status_counts': df['direct_yahoo_identity_status'].value_counts().to_dict(),
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
    }
    ap = root / AUDIT
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('V21_ACTIONS_DIRECT_ENRICHMENT_OK', audit)
    return audit


if __name__ == '__main__':
    apply()
