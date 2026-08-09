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


def capture(store: CaptureStore) -> dict:
    market = store.market()
    if market.empty:
        store.add_health("INTERNAL_FROM_FREE_OHLCV", "NO_INPUT")
        return {"status": "NO_INPUT", "facts_added": 0}
    market["date"] = pd.to_datetime(market["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        market[c] = pd.to_numeric(market[c], errors="coerce")
    # One observation per ISIN/date: official first, then API fallbacks.
    priority = {"EURONEXT_DELAYED": 1, "DEUTSCHE_BOERSE_DELAYED": 1, "TWELVEDATA_FREE": 2, "MARKETSTACK_FREE": 3}
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
        values = {
            "last_close": float(close.iloc[-1]),
            "high_52w": float(window_1y["high"].max()) if window_1y["high"].notna().any() else float(close.max()),
            "low_52w": float(window_1y["low"].min()) if window_1y["low"].notna().any() else float(close.min()),
            "perf_1m_pct": _pct(close, 21), "perf_3m_pct": _pct(close, 63),
            "perf_6m_pct": _pct(close, 126), "perf_1y_pct": _pct(close, 252),
            "mm20": float(close.tail(20).mean()) if len(close) >= 20 else None,
            "mm50": float(close.tail(50).mean()) if len(close) >= 50 else None,
            "mm200": float(close.tail(200).mean()) if len(close) >= 200 else None,
            "rsi14": _rsi(close),
            "volatility_20d": float(returns.tail(20).std(ddof=1) * np.sqrt(252) * 100) if len(close) >= 21 else None,
            "volatility_60d": float(returns.tail(60).std(ddof=1) * np.sqrt(252) * 100) if len(close) >= 61 else None,
            "max_drawdown_1y": float(dd.min()) if not dd.empty else None,
            "rvol20": float(volume.iloc[-1] / volume.tail(20).mean()) if len(volume) >= 20 and volume.tail(20).mean() > 0 else None,
        }
        for field, value in values.items():
            if value is None or not np.isfinite(value):
                continue
            rows.append({
                "isin": str(isin), "field": field, "value": value, "value_text": "", "as_of": today,
                "source": "INTERNAL_FROM_FREE_OHLCV", "evidence": "DERIVED", "confidence": 0.90,
                "status": "DERIVED", "observed_at_utc": utcnow(),
            })
    added = store.upsert_facts(rows)
    store.add_health("INTERNAL_FROM_FREE_OHLCV", "OK" if added else "NO_NEW_DATA", processed, added, 0,
                     message="No external calls; official/API free OHLCV deduplicated by source priority")
    return {"status": "OK", "instruments": processed, "facts_added": added}
