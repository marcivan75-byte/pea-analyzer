from __future__ import annotations

import numpy as np
import pandas as pd


REQUIRED_OHLCV = {"open", "high", "low", "close", "volume"}


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    missing = REQUIRED_OHLCV - set(out.columns)
    if missing:
        raise ValueError(f"TCT intraday OHLCV columns missing: {sorted(missing)}")
    for col in REQUIRED_OHLCV | {"bid", "ask", "bid_size", "ask_size"}:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    return out[~out.index.duplicated(keep="last")]


def _session_transform(values: pd.Series, sessions: pd.Series, func) -> pd.Series:
    return values.groupby(sessions, group_keys=False).transform(func)


def compute_intraday_features(frame: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Build causal five-minute features for TCT V24.2.0 SHADOW.

    Every rolling reference used for a possible signal is shifted before being
    compared with the current bar. Opening-range values are actionable only
    after the configured opening range has completed.
    """
    df = _normalise(frame)
    if df.empty:
        return df

    icfg = cfg["intraday_data"]
    opening_bars = int(icfg["opening_range_bars"])
    rvol_lookback = int(icfg["rvol_lookback_sessions"])
    rvol_min_sessions = int(icfg["rvol_min_sessions"])
    breakout_lookback = int(icfg["breakout_lookback_bars"])

    # Preserve the provider/exchange timezone. Do not impose Paris time on all
    # PEA venues; the provider's local session date is the grouping boundary.
    session_key = pd.Series(pd.Index(df.index.date), index=df.index, dtype=object)
    df["session_date"] = session_key.astype(str)
    df["bar_number"] = session_key.groupby(session_key).cumcount().astype(int)

    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    tpv = typical * df["volume"].fillna(0.0)
    cum_tpv = tpv.groupby(session_key).cumsum()
    cum_vol = df["volume"].fillna(0.0).groupby(session_key).cumsum()
    df["vwap"] = cum_tpv / cum_vol.replace(0, np.nan)
    df["vwap_distance_pct"] = df["close"] / df["vwap"].replace(0, np.nan) - 1.0
    df["vwap_slope_3"] = _session_transform(df["vwap"], session_key, lambda s: s.pct_change(3))

    prev_close = _session_transform(df["close"], session_key, lambda s: s.shift(1))
    prev_vwap = _session_transform(df["vwap"], session_key, lambda s: s.shift(1))
    df["vwap_reclaim"] = prev_close.notna() & prev_vwap.notna() & (prev_close <= prev_vwap) & (df["close"] > df["vwap"])

    min_breakout = max(5, breakout_lookback // 4)
    df["prior_high"] = _session_transform(
        df["high"], session_key, lambda s: s.shift(1).rolling(breakout_lookback, min_periods=min_breakout).max()
    )
    df["prior_low"] = _session_transform(
        df["low"], session_key, lambda s: s.shift(1).rolling(breakout_lookback, min_periods=min_breakout).min()
    )
    df["intraday_breakout"] = df["close"] > df["prior_high"]

    df["opening_range_high"] = np.nan
    df["opening_range_low"] = np.nan
    df["opening_range_ready"] = False
    for _, idx in session_key.groupby(session_key).groups.items():
        loc = list(idx)
        if len(loc) <= opening_bars:
            continue
        opening = df.loc[loc[:opening_bars]]
        high = pd.to_numeric(opening["high"], errors="coerce").max()
        low = pd.to_numeric(opening["low"], errors="coerce").min()
        actionable = loc[opening_bars:]
        df.loc[actionable, "opening_range_high"] = high
        df.loc[actionable, "opening_range_low"] = low
        df.loc[actionable, "opening_range_ready"] = True
    df["opening_range_breakout"] = df["opening_range_ready"].astype(bool) & (df["close"] > df["opening_range_high"])

    previous_close_global = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close_global).abs(),
            (df["low"] - previous_close_global).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["intraday_atr14"] = _session_transform(true_range, session_key, lambda s: s.rolling(14, min_periods=5).mean())
    df["intraday_atr_pct"] = df["intraday_atr14"] / df["close"].replace(0, np.nan)
    prior_range = _session_transform(true_range, session_key, lambda s: s.shift(1).rolling(10, min_periods=5).median())
    df["range_expansion_ratio"] = true_range / prior_range.replace(0, np.nan)

    df["return_1bar"] = _session_transform(df["close"], session_key, lambda s: s.pct_change())
    df["return_3bar"] = _session_transform(df["close"], session_key, lambda s: s.pct_change(3))
    df["ema9"] = _session_transform(df["close"], session_key, lambda s: s.ewm(span=9, adjust=False).mean())
    df["ema9_slope_3"] = _session_transform(df["ema9"], session_key, lambda s: s.pct_change(3))

    # Same-time-of-day RVOL. Each slot is compared only with earlier sessions.
    slot = df["bar_number"].astype(int)
    expected_volume = df["volume"].groupby(slot, group_keys=False).transform(
        lambda s: s.shift(1).rolling(rvol_lookback, min_periods=rvol_min_sessions).median()
    )
    df["expected_slot_volume"] = expected_volume
    df["rvol_slot"] = df["volume"] / expected_volume.replace(0, np.nan)

    prior_volume = _session_transform(df["volume"], session_key, lambda s: s.shift(1).rolling(5, min_periods=3).median())
    df["volume_acceleration"] = df["volume"] / prior_volume.replace(0, np.nan)

    df["turnover"] = df["close"] * df["volume"]
    expected_turnover = df["turnover"].groupby(slot, group_keys=False).transform(
        lambda s: s.shift(1).rolling(rvol_lookback, min_periods=rvol_min_sessions).median()
    )
    df["turnover_ratio"] = df["turnover"] / expected_turnover.replace(0, np.nan)

    if {"bid", "ask"}.issubset(df.columns):
        mid = (df["bid"] + df["ask"]) / 2.0
        valid = (df["ask"] >= df["bid"]) & (mid > 0)
        df["spread_pct"] = ((df["ask"] - df["bid"]) / mid).where(valid)
    else:
        df["spread_pct"] = np.nan

    if {"bid_size", "ask_size"}.issubset(df.columns):
        denom = df["bid_size"] + df["ask_size"]
        df["order_flow_imbalance"] = (df["bid_size"] - df["ask_size"]) / denom.replace(0, np.nan)
    else:
        df["order_flow_imbalance"] = np.nan

    return df
