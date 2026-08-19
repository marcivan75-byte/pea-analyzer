from __future__ import annotations

import math

import numpy as np
import pandas as pd


REQUIRED = {"open", "high", "low", "close", "volume"}


def _finite(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _clip(value: float) -> float:
    return float(np.clip(value, 0.0, 100.0))


def _scale(value: float | None, low: float, high: float) -> float | None:
    if value is None or high <= low:
        return None
    return _clip((value - low) / (high - low) * 100.0)


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    out.columns = [str(c).strip().lower().replace(" ", "_") for c in out.columns]
    missing = REQUIRED - set(out.columns)
    if missing:
        raise ValueError(f"TCT daily trader OHLCV columns missing: {sorted(missing)}")
    for col in REQUIRED:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    return out[~out.index.duplicated(keep="last")]


def _weighted(components: dict[str, float | None], weights: dict[str, float]) -> tuple[float | None, float]:
    numerator = 0.0
    observed = 0.0
    total = float(sum(weights.values()))
    for key, weight in weights.items():
        value = _finite(components.get(key))
        if value is None:
            continue
        numerator += _clip(value) * float(weight)
        observed += float(weight)
    if observed <= 0 or total <= 0:
        return None, 0.0
    return _clip(numerator / observed), float(np.clip(observed / total, 0.0, 1.0))


def _weekly_view(df: pd.DataFrame, trend_weeks: int, momentum_weeks: int) -> dict[str, float | bool | None]:
    weekly = df[["open", "high", "low", "close", "volume"]].resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    if weekly.empty:
        return {}
    current = weekly.iloc[-1]
    previous = weekly.iloc[-2] if len(weekly) >= 2 else None
    sma = weekly["close"].rolling(trend_weeks, min_periods=max(3, trend_weeks // 2)).mean()
    ret = weekly["close"].pct_change(momentum_weeks)
    wrange = float(current["high"] - current["low"])
    close_location = None if wrange <= 0 else float((current["close"] - current["low"]) / wrange)
    previous_pivot = None
    previous_high = previous_low = None
    if previous is not None:
        previous_high = float(previous["high"])
        previous_low = float(previous["low"])
        previous_pivot = float((previous["high"] + previous["low"] + previous["close"]) / 3.0)
    return {
        "weekly_close": _finite(current["close"]),
        "weekly_sma": _finite(sma.iloc[-1]),
        "weekly_return": _finite(ret.iloc[-1]),
        "weekly_close_location": close_location,
        "previous_week_high": previous_high,
        "previous_week_low": previous_low,
        "previous_week_pivot": previous_pivot,
    }


def compute_daily_weekly_trader_snapshot(frame: pd.DataFrame, cfg: dict) -> dict:
    """Compute TCT trader-inspired diagnostics using daily data only.

    The function deliberately contains no intraday resampling, bid/ask, order-book,
    or quasi-real-time dependency. Weekly values are derived causally from the
    daily bars available at the evaluation date.
    """
    df = _normalise(frame)
    minimum = int(cfg["data_policy"]["minimum_daily_bars"])
    if len(df) < minimum:
        return {"status": "DATA_INSUFFICIENT", "bars": int(len(df))}

    lb = cfg["lookbacks"]
    atr_n = int(lb["atr"])
    vol_n = int(lb["volume"])
    fast_breakout = int(lb["breakout_fast"])
    slow_breakout = int(lb["breakout_slow"])
    retest_sessions = int(lb["retest_sessions"])
    ema_fast_n = int(lb["fast_ema"])
    ema_trend_n = int(lb["trend_ema"])
    vw_fast_n = int(lb["volume_weighted_price_fast"])
    vw_slow_n = int(lb["volume_weighted_price_slow"])

    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(atr_n, min_periods=max(5, atr_n // 2)).mean()
    daily_range = df["high"] - df["low"]
    prior_range_median = daily_range.shift(1).rolling(vol_n, min_periods=10).median()

    volume_median = df["volume"].shift(1).rolling(vol_n, min_periods=10).median()
    volume_avg5 = df["volume"].shift(1).rolling(5, min_periods=3).mean()
    volume_avg20 = df["volume"].shift(1).rolling(vol_n, min_periods=10).mean()
    turnover = df["close"] * df["volume"]
    median_turnover20 = turnover.shift(1).rolling(vol_n, min_periods=10).median()

    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vw_num = typical * df["volume"].fillna(0.0)
    rvwap20 = vw_num.rolling(vw_fast_n, min_periods=max(10, vw_fast_n // 2)).sum() / df["volume"].rolling(
        vw_fast_n, min_periods=max(10, vw_fast_n // 2)
    ).sum().replace(0, np.nan)
    rvwap60 = vw_num.rolling(vw_slow_n, min_periods=max(20, vw_slow_n // 2)).sum() / df["volume"].rolling(
        vw_slow_n, min_periods=max(20, vw_slow_n // 2)
    ).sum().replace(0, np.nan)

    ema9 = df["close"].ewm(span=ema_fast_n, adjust=False).mean()
    ema20 = df["close"].ewm(span=ema_trend_n, adjust=False).mean()
    prior_high20 = df["high"].shift(1).rolling(fast_breakout, min_periods=max(10, fast_breakout // 2)).max()
    prior_high55 = df["high"].shift(1).rolling(slow_breakout, min_periods=max(20, slow_breakout // 2)).max()
    prior_low10 = df["low"].shift(1).rolling(10, min_periods=5).min()

    breakout20_series = df["close"] > prior_high20
    breakout55_series = df["close"] > prior_high55

    current = df.iloc[-1]
    previous = df.iloc[-2]
    close = float(current["close"])
    open_ = float(current["open"])
    high = float(current["high"])
    low = float(current["low"])
    prev_close_value = float(previous["close"])
    atr_value = _finite(atr.iloc[-1])
    range_value = max(high - low, 0.0)

    daily_rvol = None
    med_vol = _finite(volume_median.iloc[-1])
    if med_vol and med_vol > 0:
        daily_rvol = float(current["volume"] / med_vol)
    volume_acceleration = None
    avg5 = _finite(volume_avg5.iloc[-1])
    avg20 = _finite(volume_avg20.iloc[-1])
    if avg5 is not None and avg20 and avg20 > 0:
        volume_acceleration = float(avg5 / avg20)

    range_expansion = None
    prior_med_range = _finite(prior_range_median.iloc[-1])
    if prior_med_range and prior_med_range > 0:
        range_expansion = float(range_value / prior_med_range)

    clv = 0.0 if range_value <= 0 else float((2.0 * close - high - low) / range_value)
    body_ratio = 0.0 if range_value <= 0 else float(abs(close - open_) / range_value)
    upper_wick_ratio = 0.0 if range_value <= 0 else float((high - max(open_, close)) / range_value)
    lower_wick_ratio = 0.0 if range_value <= 0 else float((min(open_, close) - low) / range_value)
    gap_pct = None if prev_close_value <= 0 else float(open_ / prev_close_value - 1.0)
    gap_atr = None if atr_value is None or atr_value <= 0 else float((open_ - prev_close_value) / atr_value)

    breakout20 = bool(breakout20_series.iloc[-1])
    breakout55 = bool(breakout55_series.iloc[-1])
    breakout_level = _finite(prior_high55.iloc[-1]) if breakout55 else _finite(prior_high20.iloc[-1]) if breakout20 else None

    retest = False
    retest_level = None
    prior_breakouts = breakout20_series.iloc[max(0, len(df) - retest_sessions - 1):-1]
    true_positions = np.flatnonzero(prior_breakouts.fillna(False).to_numpy())
    if len(true_positions):
        relative_pos = int(true_positions[-1])
        absolute_pos = max(0, len(df) - retest_sessions - 1) + relative_pos
        level = _finite(prior_high20.iloc[absolute_pos])
        if level is not None and atr_value is not None:
            tolerance = max(0.005 * close, 0.50 * atr_value)
            if low <= level + tolerance and close >= level:
                retest = True
                retest_level = level

    previous_pivot = float((previous["high"] + previous["low"] + previous["close"]) / 3.0)
    previous_r1 = float(2.0 * previous_pivot - previous["low"])
    previous_s1 = float(2.0 * previous_pivot - previous["high"])

    weekly = _weekly_view(df, int(lb["weekly_trend_weeks"]), int(lb["weekly_momentum_weeks"]))
    weekly_close = _finite(weekly.get("weekly_close"))
    weekly_sma = _finite(weekly.get("weekly_sma"))
    weekly_return = _finite(weekly.get("weekly_return"))
    weekly_close_location = _finite(weekly.get("weekly_close_location"))

    rvwap20_value = _finite(rvwap20.iloc[-1])
    rvwap60_value = _finite(rvwap60.iloc[-1])
    distance_rvwap20_atr = None
    if rvwap20_value is not None and atr_value and atr_value > 0:
        distance_rvwap20_atr = float((close - rvwap20_value) / atr_value)

    return5 = _finite(df["close"].pct_change(5).iloc[-1])
    return20 = _finite(df["close"].pct_change(20).iloc[-1])
    ema9_value = _finite(ema9.iloc[-1])
    ema20_value = _finite(ema20.iloc[-1])
    ema9_slope5 = _finite(ema9.pct_change(5).iloc[-1])

    # Entry components -----------------------------------------------------
    structure_candidates: list[float] = []
    if breakout55:
        structure_candidates.append(100.0)
    if breakout20:
        structure_candidates.append(92.0)
    if retest:
        structure_candidates.append(96.0)
    prior20 = _finite(prior_high20.iloc[-1])
    if prior20 and prior20 > 0 and not structure_candidates:
        proximity = close / prior20
        structure_candidates.append(_clip(50.0 + (proximity - 0.95) / 0.05 * 35.0))
    structure_score = max(structure_candidates) if structure_candidates else 40.0

    rvol_score = _scale(daily_rvol, 0.8, 2.0)
    accel_score = _scale(volume_acceleration, 0.8, 1.6)
    median_turnover = _finite(median_turnover20.iloc[-1])
    liquidity_floor = float(cfg["shadow_thresholds"]["minimum_median_turnover_eur_research"])
    if median_turnover is None:
        turnover_score = None
    elif median_turnover < liquidity_floor:
        turnover_score = _clip(median_turnover / max(liquidity_floor, 1.0) * 50.0)
    else:
        turnover_score = _clip(60.0 + 20.0 * math.log10(max(median_turnover / liquidity_floor, 1.0)))
    volume_values = [v for v in (rvol_score, accel_score, turnover_score) if v is not None]
    volume_score = float(np.mean(volume_values)) if volume_values else None

    close_location_score = _clip((clv + 1.0) * 50.0)
    candle_direction_bonus = 15.0 if close >= open_ else -15.0
    wick_bonus = (lower_wick_ratio - upper_wick_ratio) * 25.0
    price_action_score = _clip(close_location_score + candle_direction_bonus + wick_bonus + body_ratio * 10.0)

    range_score = _scale(range_expansion, 0.7, 1.8)
    atr_pct = None if atr_value is None or close <= 0 else float(atr_value / close)
    if atr_pct is None:
        atr_score = None
    elif atr_pct < 0.008:
        atr_score = _clip(atr_pct / 0.008 * 50.0)
    elif atr_pct <= 0.05:
        atr_score = 85.0
    else:
        atr_score = _clip(85.0 - (atr_pct - 0.05) / 0.05 * 60.0)
    prior_compression = False
    if len(df) >= 8:
        recent_ranges = daily_range.iloc[-6:-1]
        recent_baseline = daily_range.shift(1).rolling(20, min_periods=10).median().iloc[-6:-1]
        ratios = recent_ranges / recent_baseline.replace(0, np.nan)
        prior_compression = bool((ratios < 0.75).any())
    expansion_after_compression = bool(prior_compression and range_expansion is not None and range_expansion >= 1.10)
    vol_values = [v for v in (range_score, atr_score) if v is not None]
    volatility_score = float(np.mean(vol_values)) if vol_values else None
    if volatility_score is not None and expansion_after_compression:
        volatility_score = _clip(volatility_score + 10.0)

    momentum_values: list[float] = []
    if return5 is not None:
        momentum_values.append(_clip(50.0 + return5 * 800.0))
    if return20 is not None:
        momentum_values.append(_clip(50.0 + return20 * 400.0))
    if ema9_value is not None:
        momentum_values.append(75.0 if close >= ema9_value else 25.0)
    if ema20_value is not None:
        momentum_values.append(80.0 if close >= ema20_value else 20.0)
    if ema9_slope5 is not None:
        momentum_values.append(_clip(50.0 + ema9_slope5 * 2500.0))
    momentum_score = float(np.mean(momentum_values)) if momentum_values else None

    weekly_values: list[float] = []
    if weekly_sma is not None and weekly_close is not None:
        weekly_values.append(85.0 if weekly_close >= weekly_sma else 25.0)
    if weekly_return is not None:
        weekly_values.append(_clip(50.0 + weekly_return * 350.0))
    if weekly_close_location is not None:
        weekly_values.append(_clip(weekly_close_location * 100.0))
    weekly_score = float(np.mean(weekly_values)) if weekly_values else None

    vw_values: list[float] = []
    if rvwap20_value is not None:
        vw_values.append(80.0 if close >= rvwap20_value else 25.0)
    if rvwap60_value is not None:
        vw_values.append(85.0 if close >= rvwap60_value else 30.0)
    vw_score = float(np.mean(vw_values)) if vw_values else None

    entry_components = {
        "structure_breakout_retest": structure_score,
        "volume_liquidity_confirmation": volume_score,
        "price_action_close_quality": price_action_score,
        "volatility_setup": volatility_score,
        "momentum_trend": momentum_score,
        "weekly_alignment": weekly_score,
        "volume_weighted_price_position": vw_score,
    }
    entry_score, entry_coverage = _weighted(entry_components, cfg["entry_weights"])

    # Exit-risk components -------------------------------------------------
    failed_breakout = bool(retest_level is not None and close < retest_level)
    if not failed_breakout and prior20 is not None and breakout20_series.iloc[-2] and close < prior20:
        failed_breakout = True
    failed_breakout_score = 100.0 if failed_breakout else 15.0

    below_fast = int(ema9_value is not None and close < ema9_value)
    below_trend = int(ema20_value is not None and close < ema20_value)
    fast_trend_risk = _clip((below_fast * 45.0) + (below_trend * 55.0))

    distribution = bool(close < prev_close_value and daily_rvol is not None and daily_rvol >= 1.20 and clv < 0.0)
    distribution_score = 100.0 if distribution else 20.0 if close < prev_close_value else 5.0

    momentum_risk_values: list[float] = []
    if return5 is not None:
        momentum_risk_values.append(_clip(50.0 - return5 * 1000.0))
    if ema9_slope5 is not None:
        momentum_risk_values.append(_clip(50.0 - ema9_slope5 * 3000.0))
    momentum_risk = float(np.mean(momentum_risk_values)) if momentum_risk_values else None

    weekly_risk_values: list[float] = []
    if weekly_sma is not None and weekly_close is not None:
        weekly_risk_values.append(85.0 if weekly_close < weekly_sma else 15.0)
    if weekly_return is not None:
        weekly_risk_values.append(_clip(50.0 - weekly_return * 450.0))
    weekly_risk = float(np.mean(weekly_risk_values)) if weekly_risk_values else None

    adverse_expansion = bool(close < open_ and clv < -0.40 and range_expansion is not None and range_expansion >= 1.20)
    adverse_volatility = 100.0 if adverse_expansion else 20.0 if close < open_ else 5.0

    exit_components = {
        "failed_breakout_structure": failed_breakout_score,
        "price_below_fast_trend": fast_trend_risk,
        "distribution_volume": distribution_score,
        "momentum_deterioration": momentum_risk,
        "weekly_deterioration": weekly_risk,
        "adverse_volatility": adverse_volatility,
    }
    exit_risk_score, exit_coverage = _weighted(exit_components, cfg["exit_risk_weights"])

    th = cfg["shadow_thresholds"]
    overextended = bool(
        distance_rvwap20_atr is not None and distance_rvwap20_atr >= float(th["overextension_atr"])
    )
    excessive_gap = bool(gap_atr is not None and gap_atr >= float(th["gap_excess_atr"]))
    low_liquidity = bool(median_turnover is not None and median_turnover < liquidity_floor)

    if entry_score is None:
        entry_state = "DATA_INSUFFICIENT"
    elif low_liquidity:
        entry_state = "LIQUIDITY_WARNING_SHADOW"
    elif overextended or excessive_gap:
        entry_state = "WAIT_PULLBACK_SHADOW"
    elif entry_score >= float(th["entry_strong"]):
        entry_state = "ENTRY_STRONG_SHADOW"
    elif entry_score >= float(th["entry_ready"]):
        entry_state = "ENTRY_READY_SHADOW"
    else:
        entry_state = "WAIT_SHADOW"

    if exit_risk_score is None:
        exit_state = "DATA_INSUFFICIENT"
    elif exit_risk_score >= float(th["exit_risk_high"]):
        exit_state = "EXIT_RISK_HIGH_SHADOW"
    elif exit_risk_score >= float(th["exit_watch"]):
        exit_state = "EXIT_WATCH_SHADOW"
    else:
        exit_state = "HOLD_SUPPORTIVE_SHADOW"

    invalidation = retest_level or breakout_level or _finite(prior_low10.iloc[-1])
    invalidation_distance = None
    if invalidation is not None and close > 0:
        invalidation_distance = float(invalidation / close - 1.0)
    invalidation_too_wide = bool(
        invalidation_distance is not None
        and abs(min(invalidation_distance, 0.0)) > float(th["structural_invalidation_distance_research_ceiling_pct"])
    )

    reasons = []
    if breakout55:
        reasons.append("BREAKOUT_55D")
    elif breakout20:
        reasons.append("BREAKOUT_20D")
    if retest:
        reasons.append("BREAKOUT_RETEST")
    if daily_rvol is not None and daily_rvol >= float(th["daily_rvol_confirmation"]):
        reasons.append("DAILY_RVOL_CONFIRMED")
    if volume_acceleration is not None and volume_acceleration >= float(th["volume_acceleration_confirmation"]):
        reasons.append("VOLUME_ACCELERATION")
    if expansion_after_compression:
        reasons.append("EXPANSION_AFTER_COMPRESSION")
    if weekly_score is not None and weekly_score >= 65.0:
        reasons.append("WEEKLY_ALIGNMENT")

    warnings = []
    if overextended:
        warnings.append("OVEREXTENDED_VS_20D_VOLUME_WEIGHTED_PRICE")
    if excessive_gap:
        warnings.append("EXCESSIVE_GAP_VS_ATR")
    if low_liquidity:
        warnings.append("LOW_MEDIAN_DAILY_TURNOVER")
    if invalidation_too_wide:
        warnings.append("STRUCTURAL_INVALIDATION_DISTANCE_ABOVE_RESEARCH_CEILING")
    if failed_breakout:
        warnings.append("FAILED_BREAKOUT")
    if distribution:
        warnings.append("DISTRIBUTION_VOLUME")

    return {
        "status": "SUCCESS_SHADOW",
        "as_of_date": pd.Timestamp(df.index[-1]).date().isoformat(),
        "bars": int(len(df)),
        "entry_state": entry_state,
        "entry_score": None if entry_score is None else round(float(entry_score), 4),
        "entry_coverage": round(float(entry_coverage), 4),
        "exit_state": exit_state,
        "exit_risk_score": None if exit_risk_score is None else round(float(exit_risk_score), 4),
        "exit_coverage": round(float(exit_coverage), 4),
        "entry_components": entry_components,
        "exit_components": exit_components,
        "breakout_20d": breakout20,
        "breakout_55d": breakout55,
        "breakout_retest": retest,
        "breakout_level": breakout_level,
        "retest_level": retest_level,
        "daily_rvol": daily_rvol,
        "volume_acceleration_5v20": volume_acceleration,
        "median_turnover_20d": median_turnover,
        "atr14": atr_value,
        "atr14_pct": atr_pct,
        "range_expansion": range_expansion,
        "expansion_after_compression": expansion_after_compression,
        "close_location_value": clv,
        "body_range_ratio": body_ratio,
        "upper_wick_ratio": upper_wick_ratio,
        "lower_wick_ratio": lower_wick_ratio,
        "gap_pct": gap_pct,
        "gap_atr": gap_atr,
        "ema9": ema9_value,
        "ema20": ema20_value,
        "return_5d": return5,
        "return_20d": return20,
        "rolling_volume_weighted_price_20d": rvwap20_value,
        "rolling_volume_weighted_price_60d": rvwap60_value,
        "distance_rvwap20_atr": distance_rvwap20_atr,
        "previous_day_pivot": previous_pivot,
        "previous_day_r1": previous_r1,
        "previous_day_s1": previous_s1,
        "previous_week_high": weekly.get("previous_week_high"),
        "previous_week_low": weekly.get("previous_week_low"),
        "previous_week_pivot": weekly.get("previous_week_pivot"),
        "weekly_close": weekly_close,
        "weekly_sma10": weekly_sma,
        "weekly_return_4w": weekly_return,
        "weekly_close_location": weekly_close_location,
        "structural_invalidation_reference": invalidation,
        "structural_invalidation_distance_pct": invalidation_distance,
        "entry_reasons": "|".join(reasons),
        "warnings": "|".join(warnings),
        "intraday_data_used": False,
        "new_market_data_downloads_required": False,
        "decision_influence": 0.0,
        "score_influence": 0.0,
        "sizing_influence": 0.0,
        "stop_loss_influence": 0.0,
    }
