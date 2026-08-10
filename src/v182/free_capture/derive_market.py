from __future__ import annotations

from datetime import date
import numpy as np
import pandas as pd

from .core import CaptureStore, utcnow


def _rsi(close: pd.Series, n: int = 14) -> float | None:
    if len(close) < n + 2:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    val = 100 - (100 / (1 + rs))
    x = val.iloc[-1]
    return float(x) if pd.notna(x) else None


def _pct(close: pd.Series, sessions: int) -> float | None:
    if len(close) <= sessions or close.iloc[-sessions - 1] == 0:
        return None
    return float((close.iloc[-1] / close.iloc[-sessions - 1] - 1) * 100)


def _macd(close: pd.Series) -> tuple[float | None, float | None, float | None]:
    if len(close) < 35:
        return None, None, None
    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    line = fast - slow
    signal = line.ewm(span=9, adjust=False).mean()
    hist = line - signal
    values = [line.iloc[-1], signal.iloc[-1], hist.iloc[-1]]
    return tuple(float(v) if pd.notna(v) and np.isfinite(v) else None for v in values)


def _atr(g: pd.DataFrame, n: int = 14) -> float | None:
    if len(g) < n + 1:
        return None
    high = pd.to_numeric(g["high"], errors="coerce")
    low = pd.to_numeric(g["low"], errors="coerce")
    close = pd.to_numeric(g["close"], errors="coerce")
    prev = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    value = tr.rolling(n).mean().iloc[-1]
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def _bollinger(close: pd.Series, n: int = 20, stdev: float = 2.0) -> tuple[float | None, float | None, float | None, float | None]:
    if len(close) < n:
        return None, None, None, None
    mid = close.rolling(n).mean()
    sigma = close.rolling(n).std(ddof=0)
    upper = mid + stdev * sigma
    lower = mid - stdev * sigma
    m, u, l = mid.iloc[-1], upper.iloc[-1], lower.iloc[-1]
    if any(pd.isna(v) or not np.isfinite(v) for v in [m, u, l]):
        return None, None, None, None
    width = (u - l) / m * 100.0 if m else None
    return float(m), float(u), float(l), float(width) if width is not None else None


def _sharpe_rf0(returns: pd.Series, sessions: int = 252) -> float | None:
    r = returns.dropna().tail(sessions)
    if len(r) < 60:
        return None
    sigma = float(r.std(ddof=1))
    if not np.isfinite(sigma) or sigma <= 0:
        return None
    return float(r.mean() / sigma * np.sqrt(252.0))


def _stochastic(g: pd.DataFrame, n: int = 14) -> tuple[float | None, float | None]:
    if len(g) < n + 2:
        return None, None
    high = pd.to_numeric(g["high"], errors="coerce")
    low = pd.to_numeric(g["low"], errors="coerce")
    close = pd.to_numeric(g["close"], errors="coerce")
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    k = (close - ll) / (hh - ll).replace(0, np.nan) * 100.0
    d = k.rolling(3).mean()
    kv, dv = k.iloc[-1], d.iloc[-1]
    return (
        float(kv) if pd.notna(kv) and np.isfinite(kv) else None,
        float(dv) if pd.notna(dv) and np.isfinite(dv) else None,
    )


def capture(store: CaptureStore) -> dict:
    market = store.market()
    if market.empty:
        store.add_health("INTERNAL_FROM_FREE_OHLCV", "NO_INPUT")
        return {"status": "NO_INPUT", "facts_added": 0}
    market["date"] = pd.to_datetime(market["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        market[c] = pd.to_numeric(market[c], errors="coerce")
    # One observation per ISIN/date: official exchange data first, then guarded free API fallbacks.
    priority = {
        "EURONEXT_DELAYED": 1,
        "DEUTSCHE_BOERSE_DELAYED": 1,
        "NASDAQ_NORDIC_DELAYED": 1,
        "TWELVEDATA_FREE": 2,
        "MARKETSTACK_FREE": 3,
        "ALPHA_VANTAGE_FREE": 4,
    }
    market["_p"] = market["source"].map(priority).fillna(9)
    market = market.sort_values(["isin", "date", "_p"]).drop_duplicates(["isin", "date"], keep="first")
    rows: list[dict] = []
    today = date.today().isoformat()
    processed = 0
    for isin, g in market.groupby("isin"):
        g = g.sort_values("date").dropna(subset=["close"])
        if len(g) < 20:
            continue
        processed += 1
        close = g["close"].astype(float)
        volume = g["volume"].astype(float)
        returns = close.pct_change()
        window_1y = g.tail(252)
        peak = window_1y["close"].cummax()
        dd = (window_1y["close"] / peak - 1) * 100
        macd_line, macd_signal, macd_hist = _macd(close)
        bb_mid, bb_upper, bb_lower, bb_width = _bollinger(close)
        stoch_k, stoch_d = _stochastic(g)
        last_volume = float(volume.dropna().iloc[-1]) if volume.notna().any() else None
        avg_volume_20 = float(volume.tail(20).mean()) if len(volume) >= 20 and volume.tail(20).notna().any() else None
        values = {
            "last_close": float(close.iloc[-1]),
            "volume": last_volume,
            "volume_avg_20d": avg_volume_20,
            "high_52w": float(window_1y["high"].max()) if window_1y["high"].notna().any() else float(close.max()),
            "low_52w": float(window_1y["low"].min()) if window_1y["low"].notna().any() else float(close.min()),
            "perf_1m_pct": _pct(close, 21),
            "perf_3m_pct": _pct(close, 63),
            "perf_6m_pct": _pct(close, 126),
            "perf_1y_pct": _pct(close, 252),
            "mm20": float(close.tail(20).mean()) if len(close) >= 20 else None,
            "mm50": float(close.tail(50).mean()) if len(close) >= 50 else None,
            "mm100": float(close.tail(100).mean()) if len(close) >= 100 else None,
            "mm200": float(close.tail(200).mean()) if len(close) >= 200 else None,
            "rsi14": _rsi(close),
            "stoch_k": stoch_k,
            "stoch_d": stoch_d,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "atr14": _atr(g),
            "bollinger_mid": bb_mid,
            "bollinger_upper": bb_upper,
            "bollinger_lower": bb_lower,
            "bollinger_width_pct": bb_width,
            "sharpe_1y_rf0": _sharpe_rf0(returns),
            "volatility_20d": float(returns.tail(20).std(ddof=1) * np.sqrt(252) * 100) if len(close) >= 21 else None,
            "volatility_60d": float(returns.tail(60).std(ddof=1) * np.sqrt(252) * 100) if len(close) >= 61 else None,
            "volatility_1y_pct": float(returns.tail(252).std(ddof=1) * np.sqrt(252) * 100) if len(returns.dropna()) >= 120 else None,
            "max_drawdown_1y": float(dd.min()) if not dd.empty else None,
            "rvol20": float(last_volume / avg_volume_20) if last_volume is not None and avg_volume_20 is not None and avg_volume_20 > 0 else None,
        }
        for field, value in values.items():
            if value is None or not np.isfinite(value):
                continue
            rows.append({
                "isin": str(isin),
                "field": field,
                "value": value,
                "value_text": "",
                "as_of": today,
                "source": "INTERNAL_FROM_FREE_OHLCV",
                "evidence": "DERIVED_FROM_FREE_OHLCV_NO_EXTERNAL_CALL",
                "confidence": 0.90,
                "status": "DERIVED",
                "observed_at_utc": utcnow(),
            })
    added = store.upsert_facts(rows)
    store.add_health(
        "INTERNAL_FROM_FREE_OHLCV",
        "OK" if added else "NO_NEW_DATA",
        processed,
        added,
        0,
        message="Derived price/performance/MA/RSI/Stochastic/MACD/ATR/Bollinger/Sharpe/volatility/drawdown/volume from deduplicated free OHLCV",
    )
    return {
        "status": "OK",
        "instruments": processed,
        "facts_added": added,
        "derived_fields": [
            "perf_1m_pct", "perf_3m_pct", "perf_6m_pct", "perf_1y_pct",
            "mm20", "mm50", "mm100", "mm200", "rsi14", "stoch_k", "stoch_d",
            "macd_line", "macd_signal", "macd_hist", "atr14", "bollinger_mid",
            "bollinger_upper", "bollinger_lower", "bollinger_width_pct", "sharpe_1y_rf0",
            "volatility_20d", "volatility_60d", "volatility_1y_pct", "max_drawdown_1y", "rvol20"
        ],
    }
