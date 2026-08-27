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
        raise ValueError(f"ACTION CT OHLCV columns missing: {sorted(missing)}")
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


def _mean(values: list[float | None]) -> float | None:
    clean = [value for item in values if (value := _finite(item)) is not None]
    return float(np.mean(clean)) if clean else None


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=max(5, window // 2)).mean()
    loss = (-delta.clip(upper=0)).rolling(window, min_periods=max(5, window // 2)).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(loss > 0, 100.0)
    return out


def _rsi_quality(value: float | None) -> float | None:
    if value is None:
        return None
    if value < 35:
        return _clip(value / 35.0 * 30.0)
    if value < 50:
        return _clip(30.0 + (value - 35.0) / 15.0 * 35.0)
    if value <= 68:
        return _clip(65.0 + (value - 50.0) / 18.0 * 35.0)
    if value <= 78:
        return _clip(100.0 - (value - 68.0) / 10.0 * 30.0)
    return _clip(70.0 - (value - 78.0) / 12.0 * 55.0)


def _completed_weekly(df: pd.DataFrame, cfg: dict) -> dict:
    weekly = df[["open", "high", "low", "close", "volume"]].resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    if weekly.empty:
        return {}
    last_daily = pd.Timestamp(df.index[-1])
    current_complete = bool(last_daily.weekday() == 4)
    completed = weekly if current_complete else weekly.iloc[:-1]
    if completed.empty:
        return {"current_week_complete": current_complete}

    lb = cfg["lookbacks"]
    fast = int(lb["weekly_trend_fast_weeks"])
    slow = int(lb["weekly_trend_slow_weeks"])
    mom_fast = int(lb["weekly_momentum_fast_weeks"])
    mom_slow = int(lb["weekly_momentum_slow_weeks"])
    close = completed["close"]
    sma_fast = close.rolling(fast, min_periods=max(4, fast // 2)).mean()
    sma_slow = close.rolling(slow, min_periods=max(8, slow // 2)).mean()
    ret_fast = close.pct_change(mom_fast)
    ret_slow = close.pct_change(mom_slow)
    latest = completed.iloc[-1]
    span = float(latest["high"] - latest["low"])
    location = None if span <= 0 else float((latest["close"] - latest["low"]) / span)
    return {
        "current_week_complete": current_complete,
        "weekly_close": _finite(latest["close"]),
        "weekly_sma_fast": _finite(sma_fast.iloc[-1]),
        "weekly_sma_slow": _finite(sma_slow.iloc[-1]),
        "weekly_return_fast": _finite(ret_fast.iloc[-1]),
        "weekly_return_slow": _finite(ret_slow.iloc[-1]),
        "weekly_close_location": location,
    }


def _weekly_score(ctx: dict) -> float | None:
    close = _finite(ctx.get("weekly_close"))
    fast = _finite(ctx.get("weekly_sma_fast"))
    slow = _finite(ctx.get("weekly_sma_slow"))
    ret_fast = _finite(ctx.get("weekly_return_fast"))
    ret_slow = _finite(ctx.get("weekly_return_slow"))
    loc = _finite(ctx.get("weekly_close_location"))
    values: list[float | None] = []
    if close is not None and fast is not None:
        values.append(85.0 if close >= fast else 25.0)
    if fast is not None and slow is not None:
        values.append(85.0 if fast >= slow else 25.0)
    if ret_fast is not None:
        values.append(_clip(50.0 + ret_fast * 300.0))
    if ret_slow is not None:
        values.append(_clip(50.0 + ret_slow * 180.0))
    if loc is not None:
        values.append(_clip(loc * 100.0))
    return _mean(values)


def _context_num(context: dict, key: str) -> float | None:
    return _finite(context.get(key)) if context else None


def _bounded_context_score(value: float | None) -> float | None:
    if value is None:
        return None
    if 0.0 <= value <= 100.0:
        return value
    return None


def _target_upside_score(value: float | None) -> float | None:
    return None if value is None else _clip(50.0 + value * 2.0)


def _revision_score(delta: float | None, upgrades: float | None) -> float | None:
    values: list[float | None] = []
    if delta is not None:
        values.append(_clip(50.0 + delta * 10.0))
    if upgrades is not None:
        values.append(_clip(50.0 + upgrades * 10.0))
    return _mean(values)


def _recent_breakout(df: pd.DataFrame, cfg: dict, atr: float | None) -> dict:
    lb = cfg["lookbacks"]
    fast = int(lb["breakout_fast"])
    slow = int(lb["breakout_slow"])
    sessions = int(lb["retest_sessions"])
    prior_fast = df["high"].shift(1).rolling(fast, min_periods=max(30, fast // 2)).max()
    prior_slow = df["high"].shift(1).rolling(slow, min_periods=max(60, slow // 2)).max()
    bfast = df["close"] > prior_fast
    bslow = df["close"] > prior_slow
    current_level = _finite(prior_slow.iloc[-1]) if bool(bslow.iloc[-1]) else _finite(prior_fast.iloc[-1]) if bool(bfast.iloc[-1]) else None
    recent_level = recent_age = None
    recent_kind = None
    start = max(0, len(df) - sessions - 1)
    for pos in range(len(df) - 2, start - 1, -1):
        if bool(bslow.iloc[pos]):
            recent_level, recent_kind = _finite(prior_slow.iloc[pos]), "120D"
            recent_age = int(len(df) - 1 - pos)
            break
        if bool(bfast.iloc[pos]):
            recent_level, recent_kind = _finite(prior_fast.iloc[pos]), "55D"
            recent_age = int(len(df) - 1 - pos)
            break
    close = float(df["close"].iloc[-1])
    low = float(df["low"].iloc[-1])
    retest = failed = False
    if recent_level is not None and atr is not None and atr > 0:
        tolerance = max(0.0075 * close, 0.60 * atr)
        retest = bool(low <= recent_level + tolerance and close >= recent_level)
        failed = bool(close < recent_level - 0.35 * atr)
    return {
        "breakout_55d": bool(bfast.iloc[-1]),
        "breakout_120d": bool(bslow.iloc[-1]),
        "breakout_level": current_level or recent_level,
        "recent_breakout_kind": recent_kind,
        "recent_breakout_age": recent_age,
        "retest": retest,
        "failed_breakout": failed,
    }


def compute_action_ct_snapshot(frame: pd.DataFrame, cfg: dict, context: dict | None = None) -> dict:
    """Compute an Action CT 2-12 week SHADOW diagnostic from completed daily bars only."""
    df = _normalise(frame)
    minimum = int(cfg["data_policy"]["minimum_daily_bars"])
    if len(df) < minimum:
        return {"status": "DATA_INSUFFICIENT", "bars": int(len(df))}

    context = context or {}
    lb = cfg["lookbacks"]
    th = cfg["shadow_thresholds"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_series = tr.rolling(int(lb["atr"]), min_periods=7).mean()
    atr = _finite(atr_series.iloc[-1])
    current = df.iloc[-1]
    previous = df.iloc[-2]
    price = float(current["close"])
    open_ = float(current["open"])
    high = float(current["high"])
    low = float(current["low"])
    prev_close_value = float(previous["close"])

    sma20 = close.rolling(int(lb["sma_fast"]), min_periods=10).mean()
    sma50 = close.rolling(int(lb["sma_medium"]), min_periods=25).mean()
    sma120 = close.rolling(int(lb["sma_slow"]), min_periods=60).mean()
    s20 = _finite(sma20.iloc[-1])
    s50 = _finite(sma50.iloc[-1])
    s120 = _finite(sma120.iloc[-1])
    slope20 = _finite(sma20.pct_change(10).iloc[-1])
    slope50 = _finite(sma50.pct_change(20).iloc[-1])

    ret10 = _finite(close.pct_change(int(lb["momentum_fast"])).iloc[-1])
    ret20 = _finite(close.pct_change(int(lb["momentum_medium"])).iloc[-1])
    ret60 = _finite(close.pct_change(int(lb["momentum_slow"])).iloc[-1])
    rsi14 = _finite(_rsi(close, 14).iloc[-1])
    momentum_accel = None
    if len(close) >= 21:
        current10 = _finite(close.iloc[-1] / close.iloc[-11] - 1.0)
        prior10 = _finite(close.iloc[-11] / close.iloc[-21] - 1.0)
        if current10 is not None and prior10 is not None:
            momentum_accel = current10 - prior10

    breakout = _recent_breakout(df, cfg, atr)
    trend_values: list[float | None] = []
    if s20 is not None:
        trend_values.append(90.0 if price >= s20 else 20.0)
    if s20 is not None and s50 is not None:
        trend_values.append(90.0 if s20 >= s50 else 25.0)
    if s50 is not None and s120 is not None:
        trend_values.append(90.0 if s50 >= s120 else 25.0)
    if slope20 is not None:
        trend_values.append(_clip(50.0 + slope20 * 1800.0))
    if slope50 is not None:
        trend_values.append(_clip(50.0 + slope50 * 1200.0))
    trend_score = _mean(trend_values)
    if breakout["breakout_120d"]:
        trend_score = max(trend_score or 0.0, 100.0)
    elif breakout["breakout_55d"]:
        trend_score = max(trend_score or 0.0, 94.0)
    if breakout["retest"]:
        trend_score = max(trend_score or 0.0, 92.0)
    if breakout["failed_breakout"]:
        trend_score = min(trend_score or 100.0, 20.0)

    momentum_score = _mean(
        [
            None if ret10 is None else _clip(50.0 + ret10 * 500.0),
            None if ret20 is None else _clip(50.0 + ret20 * 300.0),
            None if ret60 is None else _clip(50.0 + ret60 * 150.0),
            _rsi_quality(rsi14),
            None if momentum_accel is None else _clip(50.0 + momentum_accel * 600.0),
        ]
    )

    weekly = _completed_weekly(df, cfg)
    weekly_score = _weekly_score(weekly)

    volume_window = int(lb["volume"])
    prior_median_vol = _finite(df["volume"].shift(1).rolling(volume_window, min_periods=10).median().iloc[-1])
    prior_avg_vol = _finite(df["volume"].shift(1).rolling(volume_window, min_periods=10).mean().iloc[-1])
    avg5 = _finite(df["volume"].rolling(5, min_periods=3).mean().iloc[-1])
    rvol = float(current["volume"] / prior_median_vol) if prior_median_vol and prior_median_vol > 0 else None
    volume_accel = float(avg5 / prior_avg_vol) if avg5 is not None and prior_avg_vol and prior_avg_vol > 0 else None
    turnover = df["close"] * df["volume"]
    median_turnover = _finite(turnover.shift(1).rolling(volume_window, min_periods=10).median().iloc[-1])
    floor = float(th["minimum_median_turnover_eur_research"])
    if median_turnover is None:
        turnover_score = None
    elif median_turnover < floor:
        turnover_score = _clip(median_turnover / max(floor, 1.0) * 50.0)
    else:
        turnover_score = _clip(60.0 + 20.0 * math.log10(max(median_turnover / floor, 1.0)))
    volume_score = _mean([_scale(rvol, 0.8, 1.8), _scale(volume_accel, 0.8, 1.5), turnover_score])

    sector_score = _mean(
        [
            _bounded_context_score(_context_num(context, "sector_rotation_score")),
            _bounded_context_score(_context_num(context, "action_catchup_score")),
            _bounded_context_score(_context_num(context, "relative_strength")),
        ]
    )
    market_regime = _bounded_context_score(_context_num(context, "market_high_regime_score"))
    if sector_score is not None and market_regime is not None:
        sector_score = 0.75 * sector_score + 0.25 * market_regime

    consensus_score = _bounded_context_score(_context_num(context, "consensus_score_100_v21"))
    target_score = _target_upside_score(_context_num(context, "target_upside_pct_v21"))
    revision_score = _revision_score(
        _context_num(context, "consensus_delta_4w"), _context_num(context, "net_upgrades_30d_v21")
    )
    news_score = _bounded_context_score(_context_num(context, "news_catalyst_score"))
    earnings_score = _bounded_context_score(_context_num(context, "earnings_catalyst_score"))
    catalyst_score = _mean([consensus_score, target_score, revision_score, news_score, earnings_score])
    entry_components = {
        "trend_structure": trend_score,
        "momentum_quality": momentum_score,
        "weekly_alignment": weekly_score,
        "relative_strength_sector": sector_score,
        "volume_liquidity": volume_score,
        "catalyst_consensus": catalyst_score,
    }
    entry_score, entry_coverage = _weighted(entry_components, cfg["entry_weights"])

    distribution_median = df["volume"].shift(1).rolling(20, min_periods=10).median()
    recent_distribution = ((close < close.shift(1)) & (df["volume"] > distribution_median)).iloc[-3:]
    distribution_count = int(recent_distribution.fillna(False).sum())
    daily_range = max(high - low, 0.0)
    upper_wick = 0.0 if daily_range <= 0 else float((high - max(open_, price)) / daily_range)
    close_location = 0.0 if daily_range <= 0 else float((price - low) / daily_range)

    trend_break_values: list[float | None] = []
    if s20 is not None:
        trend_break_values.append(85.0 if price < s20 else 10.0)
    if s50 is not None:
        trend_break_values.append(100.0 if price < s50 else 5.0)
    if slope20 is not None:
        trend_break_values.append(_clip(50.0 - slope20 * 1800.0))
    if breakout["failed_breakout"]:
        trend_break_values.append(100.0)
    trend_break_score = _mean(trend_break_values)

    momentum_deterioration = _mean(
        [
            None if ret10 is None else _clip(50.0 - ret10 * 500.0),
            None if ret20 is None else _clip(50.0 - ret20 * 300.0),
            None if rsi14 is None else _clip(80.0 - rsi14) if rsi14 < 50 else _clip(30.0 - (rsi14 - 50.0)),
            None if momentum_accel is None else _clip(50.0 - momentum_accel * 600.0),
        ]
    )

    weekly_deterioration_values: list[float | None] = []
    wclose = _finite(weekly.get("weekly_close"))
    wfast = _finite(weekly.get("weekly_sma_fast"))
    wslow = _finite(weekly.get("weekly_sma_slow"))
    wret_fast = _finite(weekly.get("weekly_return_fast"))
    wret_slow = _finite(weekly.get("weekly_return_slow"))
    if wclose is not None and wfast is not None:
        weekly_deterioration_values.append(85.0 if wclose < wfast else 10.0)
    if wfast is not None and wslow is not None:
        weekly_deterioration_values.append(90.0 if wfast < wslow else 10.0)
    if wret_fast is not None:
        weekly_deterioration_values.append(_clip(50.0 - wret_fast * 300.0))
    if wret_slow is not None:
        weekly_deterioration_values.append(_clip(50.0 - wret_slow * 180.0))
    weekly_deterioration = _mean(weekly_deterioration_values)

    distribution_score = _clip(distribution_count / 3.0 * 100.0 + (20.0 if upper_wick >= 0.35 and rvol and rvol >= 1.15 else 0.0))
    relative_deterioration = None if sector_score is None else _clip(100.0 - sector_score)
    atr_pct = None if atr is None or price <= 0 else atr / price
    gap_atr = None if atr is None or atr <= 0 else (open_ - prev_close_value) / atr
    volatility_risk = _mean(
        [
            None if atr_pct is None else _scale(atr_pct, 0.025, 0.08),
            None if gap_atr is None else _scale(abs(gap_atr), 0.75, 2.5),
            80.0 if price < float(previous["low"]) else 10.0,
        ]
    )

    exit_components = {
        "trend_break": trend_break_score,
        "momentum_deterioration": momentum_deterioration,
        "weekly_deterioration": weekly_deterioration,
        "distribution_volume": distribution_score,
        "relative_strength_deterioration": relative_deterioration,
        "volatility_risk": volatility_risk,
    }
    exit_score, exit_coverage = _weighted(exit_components, cfg["exit_risk_weights"])

    active_level = _finite(breakout.get("breakout_level"))
    prior_low20 = _finite(df["low"].shift(1).rolling(20, min_periods=10).min().iloc[-1])
    invalidation_candidates = [x for x in (active_level, s20, s50, prior_low20) if x is not None and x < price]
    structural_invalidation = max(invalidation_candidates) if invalidation_candidates else None
    invalidation_distance = None if structural_invalidation is None or price <= 0 else structural_invalidation / price - 1.0
    invalidation_too_wide = bool(
        invalidation_distance is not None
        and abs(min(invalidation_distance, 0.0)) > float(th["structural_invalidation_distance_research_ceiling_pct"])
    )
    overextension_atr = None if atr is None or atr <= 0 or s20 is None else (price - s20) / atr
    overextended = bool(overextension_atr is not None and overextension_atr >= float(th["overextension_atr"]))
    excessive_gap = bool(gap_atr is not None and abs(gap_atr) >= float(th["gap_excess_atr"]))
    low_liquidity = bool(median_turnover is not None and median_turnover < floor)
    weekly_adverse = bool(weekly_score is not None and weekly_score < float(th["weekly_adverse_max"]))
    entry_exit_conflict = bool(exit_score is not None and exit_score >= float(th["max_exit_risk_for_entry"]))

    confirmations = {
        "TREND": bool(trend_score is not None and trend_score >= 65.0 and s50 is not None and price >= s50),
        "MOMENTUM": bool(momentum_score is not None and momentum_score >= 60.0 and ret20 is not None and ret20 > 0),
        "WEEKLY": bool(weekly_score is not None and weekly_score >= float(th["weekly_alignment_min"])),
        "VOLUME": bool((rvol is not None and rvol >= float(th["daily_rvol_confirmation"])) or (volume_accel is not None and volume_accel >= float(th["volume_acceleration_confirmation"]))),
        "SECTOR": bool(sector_score is not None and sector_score >= float(th["sector_rotation_support_min"])),
        "CATALYST": bool(catalyst_score is not None and catalyst_score >= 60.0),
    }
    confirmation_count = int(sum(confirmations.values()))
    trend_trigger = confirmations["TREND"]

    valuation_discount = _bounded_context_score(_context_num(context, "valuation_discount_score"))
    sector_hot_valuation_risk = bool(
        sector_score is not None
        and sector_score >= float(th["sector_hot_warning_min"])
        and valuation_discount is not None
        and valuation_discount <= float(th["valuation_discount_risk_max"])
    )
    days_to_earnings = _context_num(context, "days_to_earnings")
    earnings_event_risk = bool(
        days_to_earnings is not None and 0 <= days_to_earnings <= float(th["earnings_event_risk_days"])
    )

    if entry_score is None or entry_coverage < float(th["minimum_entry_coverage"]):
        entry_state = "DATA_INSUFFICIENT"
    elif low_liquidity:
        entry_state = "LIQUIDITY_WARNING_SHADOW"
    elif breakout["failed_breakout"] or entry_exit_conflict:
        entry_state = "ENTRY_CONFLICT_SHADOW"
    elif weekly_adverse:
        entry_state = "WEEKLY_CONFLICT_SHADOW"
    elif invalidation_too_wide:
        entry_state = "WAIT_RISK_SHADOW"
    elif overextended or excessive_gap:
        entry_state = "WAIT_PULLBACK_SHADOW"
    elif entry_score >= float(th["entry_strong"]) and confirmation_count >= int(th["entry_strong_min_confirmations"]) and trend_trigger:
        entry_state = "ENTRY_STRONG_SHADOW"
    elif entry_score >= float(th["entry_ready"]) and confirmation_count >= int(th["entry_ready_min_confirmations"]) and trend_trigger:
        entry_state = "ENTRY_READY_SHADOW"
    else:
        entry_state = "WAIT_SHADOW"

    structural_exit_confirmation = bool(
        breakout["failed_breakout"]
        or price < float(previous["low"])
        or distribution_count >= int(th["distribution_streak_exit_confirmation"])
        or (s50 is not None and price < s50)
    )
    if exit_score is None or exit_coverage < 0.70:
        exit_state = "DATA_INSUFFICIENT"
    elif exit_score >= float(th["exit_risk_high"]) and structural_exit_confirmation:
        exit_state = "EXIT_RISK_HIGH_CANDIDATE_SHADOW"
    elif exit_score >= float(th["exit_watch"]) or structural_exit_confirmation:
        exit_state = "EXIT_WATCH_SHADOW"
    else:
        exit_state = "HOLD_SUPPORTIVE_SHADOW"

    warnings: list[str] = []
    if sector_hot_valuation_risk:
        warnings.append("SECTOR_HOT_VALUATION_RISK")
    if earnings_event_risk:
        warnings.append("EARNINGS_EVENT_WITHIN_2D")
    if overextended:
        warnings.append("OVEREXTENDED_VS_SMA20")
    if excessive_gap:
        warnings.append("EXCESSIVE_GAP")
    if invalidation_too_wide:
        warnings.append("STRUCTURAL_INVALIDATION_BEYOND_7PCT")

    return {
        "status": "SUCCESS_SHADOW",
        "bars": int(len(df)),
        "snapshot_date": pd.Timestamp(df.index[-1]).date().isoformat(),
        "reference_close": price,
        "entry_score": entry_score,
        "entry_coverage": entry_coverage,
        "entry_state": entry_state,
        "entry_confirmation_count": confirmation_count,
        "entry_confirmations": "|".join(k for k, v in confirmations.items() if v),
        "exit_risk_score": exit_score,
        "exit_coverage": exit_coverage,
        "exit_state_raw": exit_state,
        "trend_score": trend_score,
        "momentum_score": momentum_score,
        "weekly_score": weekly_score,
        "sector_context_score": sector_score,
        "volume_score": volume_score,
        "catalyst_score": catalyst_score,
        "rsi14_ct": rsi14,
        "return_10d": ret10,
        "return_20d": ret20,
        "return_60d": ret60,
        "momentum_acceleration_ct": momentum_accel,
        "sma20_ct": s20,
        "sma50_ct": s50,
        "sma120_ct": s120,
        "sma20_slope10_ct": slope20,
        "sma50_slope20_ct": slope50,
        "atr14_ct": atr,
        "atr_pct_ct": atr_pct,
        "daily_rvol_ct": rvol,
        "volume_acceleration_ct": volume_accel,
        "median_turnover_20d_eur_ct": median_turnover,
        "close_location_ct": close_location,
        "breakout_55d_ct": breakout["breakout_55d"],
        "breakout_120d_ct": breakout["breakout_120d"],
        "breakout_retest_ct": breakout["retest"],
        "failed_breakout_ct": breakout["failed_breakout"],
        "structural_invalidation_ct": structural_invalidation,
        "structural_invalidation_distance_pct_ct": None if invalidation_distance is None else invalidation_distance * 100.0,
        "overextension_atr_ct": overextension_atr,
        "gap_atr_ct": gap_atr,
        "distribution_count_3d_ct": distribution_count,
        "sector_hot_valuation_risk_ct": sector_hot_valuation_risk,
        "earnings_event_risk_ct": earnings_event_risk,
        "warnings": "|".join(warnings),
        "entry_components": entry_components,
        "exit_components": exit_components,
        "intraday_data_used": False,
        "t1_t2_used": False,
        "fixed_take_profit_enabled": False,
        "fixed_stop_loss_enabled": False,
        "real_orders_enabled": False,
    }

