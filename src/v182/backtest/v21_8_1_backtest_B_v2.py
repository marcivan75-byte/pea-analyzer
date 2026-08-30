"""
V21.8.1 - Backtest B V2.1 - TCT Crash
Vrai P&L 26w avec stop -9% intraday, MAE/MFE et détection B1 volume z-score.
Repository path: src/v182/backtest/v21_8_1_backtest_B_v2.py
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class BacktestResultB:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    pnl_true: float
    hit_stop: bool
    day_stop: Optional[int]
    mae: float
    mfe: float
    is_B1_vol: bool
    is_B2_daily: bool


def _series_value(row: pd.Series, lower: str, upper: str) -> float:
    value = row.get(lower, row.get(upper, np.nan))
    return float(value) if pd.notna(value) else np.nan


def compute_mae_mfe(entry_price: float, hist_ohlc_126d: pd.DataFrame) -> Tuple[float, float]:
    """Return raw 126-day maximum adverse/favourable excursion versus entry.

    Excursions are measured on the complete forward window even when the simulated
    protective stop is hit. This deliberately preserves the evidence needed to
    evaluate whether the locked 9% stop is too tight before a later rebound.
    """
    if not np.isfinite(entry_price) or entry_price <= 0 or hist_ohlc_126d.empty:
        return np.nan, np.nan

    low_col = "low" if "low" in hist_ohlc_126d.columns else "Low" if "Low" in hist_ohlc_126d.columns else None
    high_col = "high" if "high" in hist_ohlc_126d.columns else "High" if "High" in hist_ohlc_126d.columns else None
    if low_col is None or high_col is None:
        return np.nan, np.nan

    lows = pd.to_numeric(hist_ohlc_126d[low_col], errors="coerce")
    highs = pd.to_numeric(hist_ohlc_126d[high_col], errors="coerce")
    mae = float(lows.min() / entry_price - 1.0) if lows.notna().any() else np.nan
    mfe = float(highs.max() / entry_price - 1.0) if highs.notna().any() else np.nan
    return mae, mfe


def compute_true_26w_pnl(
    entry_price: float,
    hist_ohlc_126d: pd.DataFrame,
    stop_pct: float = 0.09,
) -> Tuple[float, bool, Optional[int], float]:
    """Calculate the true 26-week return with the intraday protective stop.

    If any forward low touches ``entry * (1-stop_pct)``, the realised P&L is
    locked to ``-stop_pct`` on that actual day. Otherwise the final forward close
    is used. No close/close recovery after a stopped trade is permitted.
    """
    if not np.isfinite(entry_price) or entry_price <= 0:
        return np.nan, False, None, np.nan
    if hist_ohlc_126d.empty or len(hist_ohlc_126d) < 5:
        return np.nan, False, None, np.nan
    if not 0 < stop_pct < 1:
        raise ValueError("stop_pct must be in (0, 1)")

    stop_price = entry_price * (1.0 - stop_pct)
    for i, (_, row) in enumerate(hist_ohlc_126d.iterrows()):
        low = _series_value(row, "low", "Low")
        if np.isfinite(low) and low <= stop_price:
            return -stop_pct, True, i, stop_price

    final_row = hist_ohlc_126d.iloc[-1]
    final_close = _series_value(final_row, "close", "Close")
    pnl = final_close / entry_price - 1.0 if np.isfinite(final_close) else np.nan
    return pnl, False, None, final_close


def detect_B_v2(df_daily: pd.DataFrame) -> pd.DataFrame:
    """Detect B V2.1 using a prior-20-day volume z-score.

    B1_vol_v2 = vol_z > 3.0 AND daily close return < -1.5% AND close < SMA20.
    ``volume_avg20`` and ``volume_std20`` are calculated only from observations
    strictly before the signal day (shift(1)) to avoid self-normalisation.
    B2_daily becomes active on J+1; the legacy ``B1_vol`` name remains an alias
    so existing downstream consumers do not break.
    """
    required = {"close", "volume"}
    missing = required.difference(df_daily.columns)
    if missing:
        raise ValueError(f"Missing B V2 input columns: {sorted(missing)}")

    df = df_daily.copy()
    close = pd.to_numeric(df["close"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")

    prior_volume = volume.shift(1)
    df["volume_avg20"] = prior_volume.rolling(20, min_periods=20).mean()
    df["volume_std20"] = prior_volume.rolling(20, min_periods=20).std(ddof=0)
    df["vol_z"] = (volume - df["volume_avg20"]) / df["volume_std20"].replace(0.0, np.nan)
    df["pct_close"] = close.pct_change()
    df["sma20"] = close.rolling(20, min_periods=20).mean()

    b1 = (df["vol_z"] > 3.0) & (df["pct_close"] < -0.015) & (close < df["sma20"])
    df["B1_vol_v2"] = b1.fillna(False)
    df["B1_vol"] = df["B1_vol_v2"]
    df["B2_daily"] = df["B1_vol_v2"].shift(1).fillna(False).astype(bool)
    df["B_signal"] = df["B1_vol_v2"] | df["B2_daily"]
    df["B_signal_type"] = np.where(
        df["B1_vol_v2"],
        "B1_VOL_V2",
        np.where(df["B2_daily"], "B2_DAILY", ""),
    )
    return df


def run_backtest_B_v2(
    df_daily_ohlc: pd.DataFrame,
    stop_pct: float = 0.09,
    forward_days: int = 126,
) -> pd.DataFrame:
    """Run B V2.1 and record true return, real stop date, MAE and MFE."""
    if forward_days < 5:
        raise ValueError("forward_days must be >= 5")
    df_signals = detect_B_v2(df_daily_ohlc)
    results: list[dict[str, object]] = []

    for entry_date in df_signals.index[df_signals["B_signal"]]:
        loc = df_daily_ohlc.index.get_loc(entry_date)
        if not isinstance(loc, (int, np.integer)):
            continue
        if loc + forward_days >= len(df_daily_ohlc):
            continue

        entry_price = float(df_daily_ohlc.iloc[int(loc)]["close"])
        hist_forward = df_daily_ohlc.iloc[int(loc) + 1 : int(loc) + 1 + forward_days]
        pnl, hit_stop, day_stop, exit_price = compute_true_26w_pnl(entry_price, hist_forward, stop_pct)
        mae, mfe = compute_mae_mfe(entry_price, hist_forward)

        row = df_signals.loc[entry_date]
        exit_date = hist_forward.index[int(day_stop)] if hit_stop and day_stop is not None else hist_forward.index[-1]
        results.append(
            {
                "entry_date": entry_date,
                "exit_date": exit_date,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_true": pnl,
                "hit_stop": hit_stop,
                "day_stop": day_stop,
                "mae": mae,
                "mfe": mfe,
                "vol_z": float(row["vol_z"]) if pd.notna(row["vol_z"]) else np.nan,
                "B1_vol": bool(row["B1_vol"]),
                "B1_vol_v2": bool(row["B1_vol_v2"]),
                "B2_daily": bool(row["B2_daily"]),
            }
        )

    return pd.DataFrame(results)


if __name__ == "__main__":
    print("Module B V2.1 chargé - true P&L + MAE/MFE + volume z-score")
