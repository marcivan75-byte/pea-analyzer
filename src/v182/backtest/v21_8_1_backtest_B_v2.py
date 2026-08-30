"""
V21.8.1 - Backtest B V2 - TCT Crash - Production Ready
Corrige: vrai P&L 26w avec gestion stop -9% intraday + detection B1_vol/B2_daily
Repository path: src/v182/backtest/v21_8_1_backtest_B_v2.py
Compatible V21.13.7 (pas de modification poids/seuils canoniques)
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional, Tuple

@dataclass
class BacktestResultB:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    pnl_true: float
    hit_stop: bool
    day_stop: Optional[int]
    is_B1_vol: bool
    is_B2_daily: bool

def compute_true_26w_pnl(
    entry_price: float,
    hist_ohlc_126d: pd.DataFrame,
    stop_pct: float = 0.09
) -> Tuple[float, bool, Optional[int], float]:
    """
    Calcul VRAI gain 26 semaines.
    - Si low <= entry * (1-0.09) à n'importe quel jour dans les 126j -> P&L = -9% bloqué
    - Sinon P&L = close[J+126]/entry -1

    hist_ohlc_126d doit contenir: ['low','close'] sur 126 jours calendaires ~ 126 trading days
    """
    if hist_ohlc_126d.empty or len(hist_ohlc_126d) < 5:
        return np.nan, False, None, np.nan

    stop_price = entry_price * (1 - stop_pct)

    # Parcours intraday low
    for i, (idx, row) in enumerate(hist_ohlc_126d.iterrows()):
        low = row.get('low', row.get('Low', np.nan))
        if pd.notna(low) and low <= stop_price:
            return -stop_pct, True, i, stop_price

    final_close = hist_ohlc_126d.iloc[-1].get('close', hist_ohlc_126d.iloc[-1].get('Close'))
    pnl = (final_close / entry_price) - 1 if pd.notna(final_close) else np.nan
    return pnl, False, None, final_close


def detect_B_v2(df_daily: pd.DataFrame) -> pd.DataFrame:
    """
    Détection B V2
    B1_vol = volume > 2 * avg20 ET close_change < -1.5%
    B2_daily = B1_vol était vrai hier (donc actif dès J+1, pas attente hebdo)
    Input: df avec ['close','volume','volume_avg20'] index date
    """
    df = df_daily.copy()
    if 'volume_avg20' not in df.columns:
        df['volume_avg20'] = df['volume'].rolling(20).mean()

    df['pct_close'] = df['close'].pct_change()

    # B1
    df['B1_vol'] = (df['volume'] > 2.0 * df['volume_avg20']) & (df['pct_close'] < -0.015)

    # B2 daily - ne pas attendre le weekly
    df['B2_daily'] = df['B1_vol'].shift(1).fillna(False)

    # Signal global B
    df['B_signal'] = df['B1_vol'] | df['B2_daily']
    df['B_signal_type'] = np.where(df['B1_vol'], 'B1_VOL', np.where(df['B2_daily'], 'B2_DAILY', ''))

    return df


def run_backtest_B_v2(
    df_daily_ohlc: pd.DataFrame,
    stop_pct: float = 0.09,
    forward_days: int = 126
) -> pd.DataFrame:
    """
    Boucle complète backtest B sur tout l'historique
    df_daily_ohlc: ['open','high','low','close','volume'] + volume_avg20
    """
    df_signals = detect_B_v2(df_daily_ohlc)
    results = []

    # On entre à la clôture du jour signal
    signal_dates = df_signals[df_signals['B_signal']].index

    for entry_date in signal_dates:
        try:
            loc = df_daily_ohlc.index.get_loc(entry_date)
        except KeyError:
            continue

        if loc + forward_days >= len(df_daily_ohlc):
            continue

        entry_price = df_daily_ohlc.iloc[loc]['close']
        hist_forward = df_daily_ohlc.iloc[loc+1 : loc+1+forward_days]

        pnl, hit_stop, day_stop, exit_price = compute_true_26w_pnl(entry_price, hist_forward, stop_pct)

        row = df_signals.loc[entry_date]
        exit_date = hist_forward.index[int(day_stop)] if hit_stop and day_stop is not None else hist_forward.index[-1]

        results.append({
            'entry_date': entry_date,
            'exit_date': exit_date,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'pnl_true': pnl,
            'hit_stop': hit_stop,
            'day_stop': day_stop,
            'B1_vol': bool(row['B1_vol']),
            'B2_daily': bool(row['B2_daily']),
        })

    return pd.DataFrame(results)


# Pour audit V21.10
if __name__ == "__main__":
    print("Module B V2 chargé - tests unitaires attendus dans pytest")
