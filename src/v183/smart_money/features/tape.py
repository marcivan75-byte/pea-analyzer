from __future__ import annotations
import numpy as np
import pandas as pd


def calculate(frame: pd.DataFrame) -> dict:
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(frame.columns):
        return {}
    f = frame.sort_index().dropna(subset=["Close", "Volume"]).copy()
    if len(f) < 21:
        return {}
    close, high, low, volume = f["Close"], f["High"], f["Low"], f["Volume"]
    dollar_volume = close * volume
    vol_mean, vol_std = volume.rolling(20).mean(), volume.rolling(20).std(ddof=0)
    dv_mean, dv_std = dollar_volume.rolling(20).mean(), dollar_volume.rolling(20).std(ddof=0)
    denom = (high - low).replace(0, np.nan)
    clv = (((close - low) - (high - close)) / denom).fillna(0.0)
    mfv = clv * volume
    ad = mfv.cumsum()
    obv = (np.sign(close.diff()).fillna(0.0) * volume).cumsum()
    cmf20 = mfv.rolling(20).sum() / volume.rolling(20).sum().replace(0, np.nan)
    return {
        "volume_z20": _z(volume.iloc[-1], vol_mean.iloc[-1], vol_std.iloc[-1]),
        "dollar_volume_z20": _z(dollar_volume.iloc[-1], dv_mean.iloc[-1], dv_std.iloc[-1]),
        "close_location_value": float(clv.iloc[-1]),
        "cmf20": _finite(cmf20.iloc[-1]),
        "obv_slope10": _slope(obv.tail(10)),
        "ad_slope10": _slope(ad.tail(10)),
        "return_1d_pct": float(close.pct_change().iloc[-1] * 100),
        "return_3d_pct": float((close.iloc[-1] / close.iloc[-4] - 1) * 100) if len(close) >= 4 else None,
        "rvol20": float(volume.iloc[-1] / vol_mean.iloc[-1]) if vol_mean.iloc[-1] else None,
        "dollar_volume": float(dollar_volume.iloc[-1]),
    }


def score(features: dict, cfg: dict, event_context: dict | None = None) -> float:
    if not features:
        return 0.0
    z = float(features.get("volume_z20") or 0.0)
    dz = float(features.get("dollar_volume_z20") or 0.0)
    clv = float(features.get("close_location_value") or 0.0)
    cmf = float(features.get("cmf20") or 0.0)
    obv = float(features.get("obv_slope10") or 0.0)
    ad = float(features.get("ad_slope10") or 0.0)
    if z < float(cfg["tape"]["min_volume_z"]):
        return 0.0
    direction = 1 if (clv > 0 and cmf >= 0) else -1 if (clv < 0 and cmf <= 0) else 0
    if direction == 0:
        return 0.0
    confirmations = sum([
        abs(clv) >= float(cfg["tape"]["min_abs_clv"]),
        direction * obv > 0,
        direction * ad > 0,
        dz >= float(cfg["tape"]["min_dollar_volume_z"]),
    ])
    raw = direction * (0.35 + 0.25 * confirmations)
    multiplier = _event_multiplier(cfg, event_context or {})
    raw *= multiplier
    cap = float(cfg["caps"]["tape"])
    return round(max(-cap, min(cap, raw)), 4)


def _event_multiplier(cfg: dict, context: dict) -> float:
    multipliers = [1.0]
    if bool(context.get("earnings_event")):
        multipliers.append(float(cfg["tape"].get("earnings_event_multiplier", 0.5)))
    if bool(context.get("index_rebalance")):
        multipliers.append(float(cfg["tape"].get("index_rebalance_multiplier", 0.55)))
    if bool(context.get("corporate_action")):
        multipliers.append(float(cfg["tape"].get("corporate_action_multiplier", 0.0)))
    return max(0.0, min(multipliers))


def _z(value, mean, std) -> float | None:
    if pd.isna(value) or pd.isna(mean) or pd.isna(std) or std == 0:
        return None
    return float((value - mean) / std)


def _finite(value) -> float | None:
    return None if pd.isna(value) or not np.isfinite(value) else float(value)


def _slope(series: pd.Series) -> float:
    y = series.astype(float).to_numpy()
    if len(y) < 2 or np.all(y == y[0]):
        return 0.0
    x = np.arange(len(y), dtype=float)
    scale = max(abs(float(y[-1])), np.std(y), 1.0)
    return float(np.polyfit(x, y / scale, 1)[0])
