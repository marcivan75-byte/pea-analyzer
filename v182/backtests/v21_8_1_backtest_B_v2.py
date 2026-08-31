"""
v182/backtests/v21_8_1_backtest_B_v2.py
V22.5 AUDIT 5/5 - Vrai P&L 26w PIT, vol_z, B2 daily J+1, MAE/MFE, early exit hook
Corrections audits: PIT only, vectorized, no yf in loop, fail-closed, MAE/MFE logged
"""
import pandas as pd
import numpy as np
from typing import Dict, Tuple

def detect_B_v2(df_daily: pd.DataFrame) -> pd.DataFrame:
    """B1 vol_z>3 + close<-1.5% + close<sma20, B2 = B1.shift(1) daily"""
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
        tr = pd.concat([(df['high']-df['low']), (df['high']-df['close'].shift()).abs(), (df['low']-df['close'].shift()).abs()], axis=1).max(axis=1)
        df['atr_14'] = tr.rolling(14).mean()
    df['atr_14_pct'] = df['atr_14'] / df['close']
    df['vol_z'] = (df['volume'] - df['volume_avg20']) / df['volume_std20'].replace(0, np.nan)
    df['ret_1d'] = df['close'].pct_change()

    df['B1_vol'] = (df['vol_z'] > 3.0) & (df['ret_1d'] < -0.015) & (df['close'] < df['sma20'])
    df['B2_daily'] = df['B1_vol'].shift(1).fillna(False)
    df['B_signal'] = df['B1_vol'] | df['B2_daily']
    df['B_signal_type'] = np.where(df['B1_vol'], 'B1_VOL', np.where(df['B2_daily'], 'B2_DAILY_J+1', 'NONE'))
    return df

def compute_true_26w_pnl(entry_price: float, hist_126d: pd.DataFrame, stop_pct: float = 0.09) -> Dict:
    """VRAI P&L 26w avec low intraday, MAE/MFE, day stop"""
    if hist_126d is None or len(hist_126d)==0 or entry_price is None or entry_price<=0:
        return {"pnl": None, "hit_stop": None, "day_stop": None, "mae": None, "mfe": None, "exit_price": None, "block_reason": "BLOCK_DATA"}
    lows = hist_126d['low'] if 'low' in hist_126d.columns else hist_126d['close']
    highs = hist_126d['high'] if 'high' in hist_126d.columns else hist_126d['close']
    closes = hist_126d['close']
    stop_level = entry_price * (1 - stop_pct)
    # hit stop
    hit_mask = lows <= stop_level
    if hit_mask.any():
        idx = hit_mask.idxmax() if hasattr(hit_mask, 'idxmax') else np.argmax(hit_mask)
        day_stop = int(np.where(hit_mask)[0][0]) + 1 if hasattr(hit_mask, 'values') else 1
        mae = (lows.min() / entry_price -1)
        mfe = (highs[:day_stop].max() / entry_price -1) if day_stop>1 else (highs.min()/entry_price-1)
        return {"pnl": -stop_pct, "hit_stop": True, "day_stop": day_stop, "mae": float(mae), "mfe": float(mfe), "exit_price": stop_level, "block_reason": None}
    # no stop
    exit_price = closes.iloc[-1]
    pnl = exit_price / entry_price -1
    mae = (lows.min() / entry_price -1)
    mfe = (highs.max() / entry_price -1)
    return {"pnl": float(pnl), "hit_stop": False, "day_stop": 126, "mae": float(mae), "mfe": float(mfe), "exit_price": float(exit_price), "block_reason": None}

def run_backtest_B_v2(df_signals: pd.DataFrame, df_prices: pd.DataFrame, stop_pct=0.09, forward=126) -> pd.DataFrame:
    """Boucle vectorisée par signal"""
    results=[]
    for idx, sig in df_signals[df_signals['B_signal']].iterrows():
        entry = sig['close']
        hist = df_prices.loc[idx:].iloc[1:forward+1] if idx in df_prices.index else pd.DataFrame()
        res = compute_true_26w_pnl(entry, hist, stop_pct)
        res.update({"date": idx, "ticker": sig.get('ticker',''), "entry": entry, "type": sig.get('B_signal_type','')})
        results.append(res)
    return pd.DataFrame(results)
