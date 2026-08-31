"""
v182/backtests/v21_8_1_backtest_B_v2.py
HEBDO AT META - backtest B v2, stop intraday conservateur, MAE/MFE sans fuite post-sortie.
"""
import pandas as pd
import numpy as np
from typing import Dict


def detect_B_v2(df_daily: pd.DataFrame) -> pd.DataFrame:
    """B1 vol_z>3 + baisse J<-1.5% + close<sma20; B2 = B1 décalé d'un jour."""
    df = df_daily.copy()
    if 'volume_avg20' not in df.columns:
        df['volume_avg20'] = df['volume'].rolling(20).mean()
    if 'volume_std20' not in df.columns:
        df['volume_std20'] = df['volume'].rolling(20).std()
    if 'sma20' not in df.columns:
        df['sma20'] = df['close'].rolling(20).mean()
    if 'sma200' not in df.columns:
        df['sma200'] = df['close'].rolling(200).mean()
    if 'atr_14' not in df.columns:
        tr = pd.concat([
            (df['high'] - df['low']),
            (df['high'] - df['close'].shift()).abs(),
            (df['low'] - df['close'].shift()).abs(),
        ], axis=1).max(axis=1)
        df['atr_14'] = tr.rolling(14).mean()
    df['atr_14_pct'] = df['atr_14'] / df['close']
    df['vol_z'] = (df['volume'] - df['volume_avg20']) / df['volume_std20'].replace(0, np.nan)
    df['ret_1d'] = df['close'].pct_change()
    df['B1_vol'] = (df['vol_z'] > 3.0) & (df['ret_1d'] < -0.015) & (df['close'] < df['sma20'])
    df['B2_daily'] = df['B1_vol'].shift(1).fillna(False)
    df['B_signal'] = df['B1_vol'] | df['B2_daily']
    df['B_signal_type'] = np.where(df['B1_vol'], 'B1_VOL', np.where(df['B2_daily'], 'B2_DAILY_J+1', 'NONE'))
    return df


def compute_true_26w_pnl(entry_price: float, hist_126d: pd.DataFrame, stop_pct: float = 0.09, expected_days: int = 126) -> Dict:
    """P&L 26 semaines avec stop intraday et exécution conservatrice des gaps sous stop."""
    block = {"pnl": None, "hit_stop": None, "day_stop": None, "mae": None, "mfe": None, "exit_price": None}
    if hist_126d is None or len(hist_126d) == 0 or entry_price is None or entry_price <= 0:
        return {**block, "block_reason": "BLOCK_DATA"}
    if 'close' not in hist_126d.columns:
        return {**block, "block_reason": "BLOCK_DATA_CLOSE"}

    lows = hist_126d['low'] if 'low' in hist_126d.columns else hist_126d['close']
    highs = hist_126d['high'] if 'high' in hist_126d.columns else hist_126d['close']
    opens = hist_126d['open'] if 'open' in hist_126d.columns else hist_126d['close']
    closes = hist_126d['close']
    stop_level = entry_price * (1 - stop_pct)
    hit_mask = lows <= stop_level

    if hit_mask.any():
        stop_pos = int(np.flatnonzero(hit_mask.to_numpy())[0])
        day_stop = stop_pos + 1
        lows_to_exit = lows.iloc[:day_stop]
        highs_to_exit = highs.iloc[:day_stop]
        open_on_stop = float(opens.iloc[stop_pos])
        exit_price = min(stop_level, open_on_stop) if open_on_stop < stop_level else stop_level
        pnl = exit_price / entry_price - 1
        mae = lows_to_exit.min() / entry_price - 1
        mfe = highs_to_exit.max() / entry_price - 1
        return {
            "pnl": float(pnl),
            "hit_stop": True,
            "day_stop": day_stop,
            "mae": float(mae),
            "mfe": float(mfe),
            "exit_price": float(exit_price),
            "block_reason": None,
        }

    if len(hist_126d) < expected_days:
        return {**block, "block_reason": f"BLOCK_DATA_INCOMPLETE_HORIZON_{len(hist_126d)}d"}

    exit_price = closes.iloc[expected_days - 1]
    lows_h = lows.iloc[:expected_days]
    highs_h = highs.iloc[:expected_days]
    pnl = exit_price / entry_price - 1
    mae = lows_h.min() / entry_price - 1
    mfe = highs_h.max() / entry_price - 1
    return {
        "pnl": float(pnl),
        "hit_stop": False,
        "day_stop": int(expected_days),
        "mae": float(mae),
        "mfe": float(mfe),
        "exit_price": float(exit_price),
        "block_reason": None,
    }


def _signal_date(idx, sig: pd.Series):
    raw = sig.get('date', idx)
    try:
        return pd.Timestamp(raw)
    except Exception:
        return None


def _future_path(df_prices: pd.DataFrame, ticker: str, signal_date, forward: int) -> pd.DataFrame:
    """Retourne uniquement le chemin futur du ticker concerné, trié chronologiquement."""
    if signal_date is None:
        return pd.DataFrame()
    prices = df_prices.copy()
    if 'ticker' in prices.columns:
        prices = prices[prices['ticker'].astype(str) == str(ticker)]
    elif ticker:
        # Univers multi-titres sans colonne ticker : impossible de garantir l'isolation.
        return pd.DataFrame()

    if 'date' in prices.columns:
        dates = pd.to_datetime(prices['date'], errors='coerce')
        if dates.isna().any():
            return pd.DataFrame()
        prices = prices.assign(_bt_date=dates).sort_values('_bt_date')
        prices = prices[prices['_bt_date'] > pd.Timestamp(signal_date)].drop(columns=['_bt_date'])
        return prices.iloc[:forward]

    if isinstance(prices.index, pd.DatetimeIndex):
        prices = prices.sort_index()
        return prices.loc[prices.index > pd.Timestamp(signal_date)].iloc[:forward]

    # Sans axe temporel explicite, le backtest ne peut pas établir le futur sans ambiguïté.
    return pd.DataFrame()


def run_backtest_B_v2(df_signals: pd.DataFrame, df_prices: pd.DataFrame, stop_pct=0.09, forward=126) -> pd.DataFrame:
    """Évalue chaque signal sur son ticker uniquement, à partir de la séance suivante."""
    if 'B_signal' not in df_signals.columns:
        raise ValueError('BLOCK_DATA_BACKTEST: B_signal missing')
    results = []
    for idx, sig in df_signals[df_signals['B_signal']].iterrows():
        entry = sig.get('close')
        ticker = str(sig.get('ticker', ''))
        signal_date = _signal_date(idx, sig)
        hist = _future_path(df_prices, ticker, signal_date, forward)
        res = compute_true_26w_pnl(entry, hist, stop_pct, expected_days=forward)
        if hist.empty and res.get('block_reason') == 'BLOCK_DATA':
            res['block_reason'] = 'BLOCK_DATA_PRICE_PATH'
        res.update({"date": signal_date, "ticker": ticker, "entry": entry, "type": sig.get('B_signal_type', '')})
        results.append(res)
    return pd.DataFrame(results)
