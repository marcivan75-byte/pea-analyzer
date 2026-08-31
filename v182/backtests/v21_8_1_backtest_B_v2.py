"""
v182/backtests/v21_8_1_backtest_B_v2.py
HEBDO AT META - backtest B v2, stop intraday conservateur, MAE/MFE sans fuite post-sortie.
"""
import pandas as pd
import numpy as np
from typing import Dict


def _detect_B_one(df_daily: pd.DataFrame) -> pd.DataFrame:
    df = df_daily.copy()
    required={'close','volume','high','low'}
    missing=required-set(df.columns)
    if missing:
        raise ValueError(f"BLOCK_DATA_B_DETECT: missing {sorted(missing)}")
    if 'date' in df.columns:
        dates=pd.to_datetime(df['date'], errors='coerce')
        if dates.isna().any():
            raise ValueError('BLOCK_DATA_B_DETECT: invalid dates')
        df=df.assign(_b_date=dates).sort_values('_b_date').drop(columns=['_b_date'])
    elif isinstance(df.index, pd.DatetimeIndex):
        df=df.sort_index()

    if 'volume_avg20' not in df.columns:
        df['volume_avg20'] = df['volume'].rolling(20, min_periods=20).mean()
    if 'volume_std20' not in df.columns:
        df['volume_std20'] = df['volume'].rolling(20, min_periods=20).std()
    if 'sma20' not in df.columns:
        df['sma20'] = df['close'].rolling(20, min_periods=20).mean()
    if 'sma200' not in df.columns:
        df['sma200'] = df['close'].rolling(200, min_periods=200).mean()
    if 'atr_14' not in df.columns:
        prev_close=df['close'].shift(1)
        tr = pd.concat([
            (df['high'] - df['low']),
            (df['high'] - prev_close).abs(),
            (df['low'] - prev_close).abs(),
        ], axis=1).max(axis=1)
        df['atr_14'] = tr.rolling(14, min_periods=14).mean()
    df['atr_14_pct'] = df['atr_14'] / df['close'].replace(0,np.nan)
    df['vol_z'] = (df['volume'] - df['volume_avg20']) / df['volume_std20'].replace(0, np.nan)
    df['ret_1d'] = df['close'].pct_change()
    df['B1_vol'] = (df['vol_z'] > 3.0) & (df['ret_1d'] < -0.015) & (df['close'] < df['sma20'])
    df['B2_daily'] = df['B1_vol'].shift(1).fillna(False).astype(bool)
    df['B_signal'] = df['B1_vol'] | df['B2_daily']
    df['B_signal_type'] = np.where(df['B1_vol'], 'B1_VOL', np.where(df['B2_daily'], 'B2_DAILY_J+1', 'NONE'))
    return df


def detect_B_v2(df_daily: pd.DataFrame) -> pd.DataFrame:
    """Détecte B par ticker sans contamination des rolling/shift entre instruments."""
    if df_daily.empty:
        return df_daily.copy()
    if 'ticker' not in df_daily.columns:
        return _detect_B_one(df_daily)
    parts=[]
    for _, group in df_daily.groupby('ticker', sort=False, dropna=False):
        if group['ticker'].isna().all():
            raise ValueError('BLOCK_DATA_B_DETECT: null ticker group')
        parts.append(_detect_B_one(group))
    out=pd.concat(parts, axis=0)
    # Restaure l'ordre d'entrée pour ne pas surprendre les appelants.
    return out.loc[df_daily.index] if df_daily.index.is_unique else out


def compute_true_26w_pnl(entry_price: float, hist_126d: pd.DataFrame, stop_pct: float = 0.09, expected_days: int = 126) -> Dict:
    """P&L 26 semaines avec stop intraday et exécution conservatrice des gaps sous stop."""
    block = {"pnl": None, "hit_stop": None, "day_stop": None, "mae": None, "mfe": None, "exit_price": None}
    if hist_126d is None or len(hist_126d) == 0 or entry_price is None or entry_price <= 0:
        return {**block, "block_reason": "BLOCK_DATA"}
    required={'open','high','low','close'}
    missing=required-set(hist_126d.columns)
    if missing:
        return {**block, "block_reason": f"BLOCK_DATA_OHLC_MISSING_{'_'.join(sorted(missing))}"}
    ohlc=hist_126d[['open','high','low','close']].apply(pd.to_numeric, errors='coerce')
    if not np.isfinite(ohlc.to_numpy(dtype=float)).all():
        return {**block, "block_reason": "BLOCK_DATA_OHLC_NONFINITE"}
    if (ohlc[['open','high','low','close']]<=0).any().any():
        return {**block, "block_reason": "BLOCK_DATA_OHLC_NONPOSITIVE"}
    if ((ohlc['low']>ohlc['high']) | (ohlc['open']<ohlc['low']) | (ohlc['open']>ohlc['high']) | (ohlc['close']<ohlc['low']) | (ohlc['close']>ohlc['high'])).any():
        return {**block, "block_reason": "BLOCK_DATA_OHLC_INCONSISTENT"}

    lows = ohlc['low']; highs = ohlc['high']; opens = ohlc['open']; closes = ohlc['close']
    stop_level = entry_price * (1 - stop_pct)
    hit_mask = lows <= stop_level

    if hit_mask.any():
        stop_pos = int(np.flatnonzero(hit_mask.to_numpy())[0])
        day_stop = stop_pos + 1
        lows_to_exit = lows.iloc[:day_stop]
        highs_to_exit = highs.iloc[:day_stop]
        open_on_stop = float(opens.iloc[stop_pos])
        exit_price = open_on_stop if open_on_stop < stop_level else stop_level
        pnl = exit_price / entry_price - 1
        mae = lows_to_exit.min() / entry_price - 1
        mfe = highs_to_exit.max() / entry_price - 1
        return {"pnl":float(pnl),"hit_stop":True,"day_stop":day_stop,"mae":float(mae),"mfe":float(mfe),"exit_price":float(exit_price),"block_reason":None}

    if len(hist_126d) < expected_days:
        return {**block, "block_reason": f"BLOCK_DATA_INCOMPLETE_HORIZON_{len(hist_126d)}d"}

    exit_price = closes.iloc[expected_days - 1]
    lows_h = lows.iloc[:expected_days]; highs_h = highs.iloc[:expected_days]
    pnl = exit_price / entry_price - 1
    mae = lows_h.min() / entry_price - 1; mfe = highs_h.max() / entry_price - 1
    return {"pnl":float(pnl),"hit_stop":False,"day_stop":int(expected_days),"mae":float(mae),"mfe":float(mfe),"exit_price":float(exit_price),"block_reason":None}


def _signal_date(idx, sig: pd.Series):
    raw = sig.get('date', idx)
    try:
        ts=pd.Timestamp(raw)
        return None if pd.isna(ts) else ts
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
        return pd.DataFrame()

    if 'date' in prices.columns:
        dates = pd.to_datetime(prices['date'], errors='coerce')
        if dates.isna().any():
            return pd.DataFrame()
        prices = prices.assign(_bt_date=dates).sort_values('_bt_date')
        if prices['_bt_date'].duplicated().any():
            return pd.DataFrame()
        prices = prices[prices['_bt_date'] > pd.Timestamp(signal_date)].drop(columns=['_bt_date'])
        return prices.iloc[:forward]

    if isinstance(prices.index, pd.DatetimeIndex):
        prices = prices.sort_index()
        if prices.index.duplicated().any():
            return pd.DataFrame()
        return prices.loc[prices.index > pd.Timestamp(signal_date)].iloc[:forward]
    return pd.DataFrame()


def run_backtest_B_v2(df_signals: pd.DataFrame, df_prices: pd.DataFrame, stop_pct=0.09, forward=126) -> pd.DataFrame:
    """Évalue chaque signal sur son ticker uniquement, à partir de la séance suivante."""
    if 'B_signal' not in df_signals.columns:
        raise ValueError('BLOCK_DATA_BACKTEST: B_signal missing')
    if forward < 1:
        raise ValueError('BLOCK_DATA_BACKTEST: forward must be >= 1')
    results = []
    for idx, sig in df_signals[df_signals['B_signal']].iterrows():
        entry = sig.get('close'); ticker = str(sig.get('ticker', '')); signal_date = _signal_date(idx, sig)
        hist = _future_path(df_prices, ticker, signal_date, forward)
        res = compute_true_26w_pnl(entry, hist, stop_pct, expected_days=forward)
        if hist.empty and res.get('block_reason') == 'BLOCK_DATA':
            res['block_reason'] = 'BLOCK_DATA_PRICE_PATH'
        res.update({"date": signal_date, "ticker": ticker, "entry": entry, "type": sig.get('B_signal_type', '')})
        results.append(res)
    return pd.DataFrame(results)
