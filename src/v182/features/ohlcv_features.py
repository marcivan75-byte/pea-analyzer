from __future__ import annotations
import numpy as np
import pandas as pd


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = -delta.clip(upper=0).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _max_drawdown(close: pd.Series) -> float | None:
    clean = close.dropna()
    if clean.empty:
        return None
    drawdown = clean / clean.cummax() - 1
    return float(drawdown.min() * 100)


def _stochastic(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> tuple[pd.Series, pd.Series]:
    lowest = low.rolling(window).min()
    highest = high.rolling(window).max()
    denom = (highest - lowest).replace(0, np.nan)
    k = (close - lowest) / denom * 100.0
    d = k.rolling(3).mean()
    return k, d


def calculate(frame: pd.DataFrame) -> dict:
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(frame.columns):
        return {}
    frame = frame.sort_index().dropna(subset=["Close"])
    if frame.empty:
        return {}

    open_ = frame["Open"]
    close = frame["Close"]
    high = frame["High"]
    low = frame["Low"]
    volume = frame["Volume"]

    result: dict[str, float | bool | None] = {}
    for window in (20, 50, 100, 200):
        result[f"mm{window}"] = float(close.rolling(window).mean().iloc[-1]) if len(close) >= window else None

    result["rsi14"] = float(_rsi(close).iloc[-1]) if len(close) >= 15 else None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - signal
    result["macd"] = float(macd.iloc[-1])
    result["macd_signal"] = float(signal.iloc[-1])
    result["macd_hist"] = float(macd_hist.iloc[-1])
    result["macd_hist_3d_ago"] = float(macd_hist.iloc[-4]) if len(macd_hist) >= 4 and pd.notna(macd_hist.iloc[-4]) else None
    result["macd_hist_change_3d"] = (
        float(macd_hist.iloc[-1] - macd_hist.iloc[-4])
        if len(macd_hist) >= 4 and pd.notna(macd_hist.iloc[-4]) else None
    )

    previous_close = close.shift(1)
    true_range = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    atr = true_range.rolling(14).mean()
    result["atr14"] = float(atr.iloc[-1]) if len(close) >= 15 and pd.notna(atr.iloc[-1]) else None
    result["atr14_pct"] = (
        float(atr.iloc[-1] / close.iloc[-1] * 100.0)
        if result["atr14"] is not None and close.iloc[-1] else None
    )
    result["opening_gap_pct"] = (
        float((open_.iloc[-1] / previous_close.iloc[-1] - 1.0) * 100.0)
        if len(close) >= 2 and pd.notna(open_.iloc[-1]) and pd.notna(previous_close.iloc[-1]) and previous_close.iloc[-1] else None
    )

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    result["bb_mid"] = float(mid.iloc[-1]) if len(close) >= 20 and pd.notna(mid.iloc[-1]) else None
    result["bb_upper"] = float(upper.iloc[-1]) if len(close) >= 20 and pd.notna(upper.iloc[-1]) else None
    result["bb_lower"] = float(lower.iloc[-1]) if len(close) >= 20 and pd.notna(lower.iloc[-1]) else None
    result["bb_bandwidth"] = float(bandwidth.iloc[-1]) if len(close) >= 20 and pd.notna(bandwidth.iloc[-1]) else None
    if len(bandwidth.dropna()) >= 100:
        hist100 = bandwidth.dropna().tail(100)
        p15 = float(hist100.quantile(0.15))
        result["bb_bandwidth_p15_100"] = p15
        last8 = bandwidth.dropna().tail(8)
        result["bb_squeeze_fraction_8"] = float((last8 <= p15).mean()) if len(last8) == 8 else None
    else:
        result["bb_bandwidth_p15_100"] = None
        result["bb_squeeze_fraction_8"] = None

    stoch_k, stoch_d = _stochastic(high, low, close)
    result["stoch_k"] = float(stoch_k.iloc[-1]) if pd.notna(stoch_k.iloc[-1]) else None
    result["stoch_d"] = float(stoch_d.iloc[-1]) if pd.notna(stoch_d.iloc[-1]) else None
    result["stoch_bull_cross_flag"] = bool(
        len(stoch_k) >= 2
        and pd.notna(stoch_k.iloc[-1]) and pd.notna(stoch_d.iloc[-1])
        and pd.notna(stoch_k.iloc[-2]) and pd.notna(stoch_d.iloc[-2])
        and stoch_k.iloc[-2] <= stoch_d.iloc[-2]
        and stoch_k.iloc[-1] > stoch_d.iloc[-1]
    )

    avg_volume = volume.rolling(20).mean()
    result["rvol20"] = float(volume.iloc[-1] / avg_volume.iloc[-1]) if len(close) >= 20 and avg_volume.iloc[-1] else None
    daily = close.pct_change()
    result["volatility_20d"] = float(daily.rolling(20).std().iloc[-1] * np.sqrt(252) * 100) if len(close) >= 21 else None
    result["volatility_60d"] = float(daily.rolling(60).std().iloc[-1] * np.sqrt(252) * 100) if len(close) >= 61 else None
    result["max_drawdown_1y"] = _max_drawdown(close.tail(252))

    periods = {
        "perf_10d_pct": 10,
        "perf_1m_pct": 21,
        "perf_3m_pct": 63,
        "perf_6m_pct": 126,
        "perf_1y_pct": 252,
        "perf_3y_pct": 756,
        "perf_5y_pct": 1260,
    }
    for field, days in periods.items():
        result[field] = float((close.iloc[-1] / close.iloc[-days] - 1) * 100) if len(close) >= days else None

    result["positive_reversal_flag"] = bool(
        result.get("rsi14") is not None
        and 30 <= result["rsi14"] < 70
        and result.get("macd_hist") is not None
        and result["macd_hist"] > 0
        and result.get("mm20") is not None
        and close.iloc[-1] > result["mm20"]
    )
    result["last_close"] = float(close.iloc[-1])
    result["volume"] = float(volume.iloc[-1])
    return result
