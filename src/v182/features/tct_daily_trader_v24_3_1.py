from __future__ import annotations

import math

import numpy as np
import pandas as pd

from v182.features.tct_daily_trader_v24_3 import (
    _clip,
    _finite,
    _normalise,
    _scale,
    _weighted,
    compute_daily_weekly_trader_snapshot as compute_v2430,
)


def _completed_week_context(df: pd.DataFrame, trend_weeks: int, momentum_weeks: int) -> dict:
    weekly = df[["open", "high", "low", "close", "volume"]].resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    if weekly.empty:
        return {}

    last_daily = pd.Timestamp(df.index[-1])
    current_complete = bool(last_daily.weekday() == 4)
    current = weekly.iloc[-1]
    completed = weekly if current_complete else weekly.iloc[:-1]

    close = sma = ret = close_location = None
    if not completed.empty:
        latest = completed.iloc[-1]
        close = _finite(latest["close"])
        sma_s = completed["close"].rolling(trend_weeks, min_periods=max(3, trend_weeks // 2)).mean()
        ret_s = completed["close"].pct_change(momentum_weeks)
        sma = _finite(sma_s.iloc[-1])
        ret = _finite(ret_s.iloc[-1])
        span = float(latest["high"] - latest["low"])
        close_location = None if span <= 0 else float((latest["close"] - latest["low"]) / span)

    current_span = float(current["high"] - current["low"])
    current_location = None if current_span <= 0 else float((current["close"] - current["low"]) / current_span)

    previous = weekly.iloc[-2] if len(weekly) >= 2 else None
    return {
        "weekly_close": close,
        "weekly_sma10": sma,
        "weekly_return_4w": ret,
        "weekly_close_location": close_location,
        "current_week_close": _finite(current["close"]),
        "current_week_close_location": current_location,
        "current_week_complete": current_complete,
        "previous_week_high": None if previous is None else _finite(previous["high"]),
        "previous_week_low": None if previous is None else _finite(previous["low"]),
        "previous_week_pivot": None
        if previous is None
        else _finite((previous["high"] + previous["low"] + previous["close"]) / 3.0),
    }


def _recent_breakout_context(
    df: pd.DataFrame,
    sessions: int,
    fast_breakout: int,
    slow_breakout: int,
) -> dict:
    prior20 = df["high"].shift(1).rolling(fast_breakout, min_periods=max(10, fast_breakout // 2)).max()
    prior55 = df["high"].shift(1).rolling(slow_breakout, min_periods=max(20, slow_breakout // 2)).max()
    b20 = df["close"] > prior20
    b55 = df["close"] > prior55

    current_level = _finite(prior55.iloc[-1]) if bool(b55.iloc[-1]) else _finite(prior20.iloc[-1]) if bool(b20.iloc[-1]) else None
    current_kind = "55D" if bool(b55.iloc[-1]) else "20D" if bool(b20.iloc[-1]) else None

    recent_level = recent_kind = recent_age = None
    start = max(0, len(df) - sessions - 1)
    for pos in range(len(df) - 2, start - 1, -1):
        if bool(b55.iloc[pos]):
            recent_level, recent_kind = _finite(prior55.iloc[pos]), "55D"
            recent_age = int(len(df) - 1 - pos)
            break
        if bool(b20.iloc[pos]):
            recent_level, recent_kind = _finite(prior20.iloc[pos]), "20D"
            recent_age = int(len(df) - 1 - pos)
            break

    return {
        "breakout_20d": bool(b20.iloc[-1]),
        "breakout_55d": bool(b55.iloc[-1]),
        "current_level": current_level,
        "current_kind": current_kind,
        "recent_level": recent_level,
        "recent_kind": recent_kind,
        "recent_age": recent_age,
        "prior_low10": _finite(df["low"].shift(1).rolling(10, min_periods=5).min().iloc[-1]),
    }


def _weekly_score(ctx: dict) -> float | None:
    values: list[float] = []
    close = _finite(ctx.get("weekly_close"))
    sma = _finite(ctx.get("weekly_sma10"))
    ret = _finite(ctx.get("weekly_return_4w"))
    loc = _finite(ctx.get("weekly_close_location"))
    current_loc = _finite(ctx.get("current_week_close_location"))
    if close is not None and sma is not None:
        values.append(85.0 if close >= sma else 25.0)
    if ret is not None:
        values.append(_clip(50.0 + ret * 350.0))
    if loc is not None:
        values.append(_clip(loc * 100.0))
    if current_loc is not None:
        values.append(_clip(current_loc * 100.0))
    return float(np.mean(values)) if values else None


def _volume_score(df: pd.DataFrame, cfg: dict, daily_rvol: float | None) -> tuple[float | None, float | None, float | None]:
    vol_n = int(cfg["lookbacks"]["volume"])
    avg5 = _finite(df["volume"].rolling(5, min_periods=3).mean().iloc[-1])
    prior20 = _finite(df["volume"].shift(1).rolling(vol_n, min_periods=10).mean().iloc[-1])
    acceleration = float(avg5 / prior20) if avg5 is not None and prior20 and prior20 > 0 else None

    turnover = df["close"] * df["volume"]
    median_turnover = _finite(turnover.shift(1).rolling(vol_n, min_periods=10).median().iloc[-1])
    floor = float(cfg["shadow_thresholds"]["minimum_median_turnover_eur_research"])
    if median_turnover is None:
        turnover_score = None
    elif median_turnover < floor:
        turnover_score = _clip(median_turnover / max(floor, 1.0) * 50.0)
    else:
        turnover_score = _clip(60.0 + 20.0 * math.log10(max(median_turnover / floor, 1.0)))

    values = [v for v in (_scale(daily_rvol, 0.8, 2.0), _scale(acceleration, 0.8, 1.6), turnover_score) if v is not None]
    return (float(np.mean(values)) if values else None), acceleration, median_turnover


def _join_unique(items: list[str]) -> str:
    return "|".join(dict.fromkeys(x for x in items if x))


def compute_daily_weekly_trader_snapshot(frame: pd.DataFrame, cfg: dict) -> dict:
    """V24.3.1 robustness layer over V24.3.0, still daily/weekly and SHADOW only."""
    snap = compute_v2430(frame, cfg)
    if snap.get("status") != "SUCCESS_SHADOW":
        return snap

    df = _normalise(frame)
    lb = cfg["lookbacks"]
    th = cfg["shadow_thresholds"]
    close = float(df["close"].iloc[-1])
    low = float(df["low"].iloc[-1])
    open_ = float(df["open"].iloc[-1])
    previous_low = float(df["low"].iloc[-2])
    atr = _finite(snap.get("atr14"))

    weekly = _completed_week_context(df, int(lb["weekly_trend_weeks"]), int(lb["weekly_momentum_weeks"]))
    weekly_score = _weekly_score(weekly)
    snap.update(weekly)

    breakout = _recent_breakout_context(
        df,
        int(lb["retest_sessions"]),
        int(lb["breakout_fast"]),
        int(lb["breakout_slow"]),
    )
    active_level = breakout["current_level"] or breakout["recent_level"]
    active_kind = breakout["current_kind"] or breakout["recent_kind"]
    active_age = 0 if breakout["current_level"] is not None else breakout["recent_age"]

    retest = False
    if breakout["recent_level"] is not None and atr is not None and atr > 0:
        tolerance = max(0.005 * close, 0.50 * atr)
        retest = bool(low <= breakout["recent_level"] + tolerance and close >= breakout["recent_level"])

    failed_breakout = False
    if breakout["recent_level"] is not None and atr is not None and atr > 0:
        failed_breakout = bool(close < breakout["recent_level"] - 0.25 * atr)

    entry_components = dict(snap.get("entry_components") or {})
    exit_components = dict(snap.get("exit_components") or {})
    structure_score = _finite(entry_components.get("structure_breakout_retest"))
    if structure_score is None:
        structure_score = 40.0
    if breakout["breakout_55d"]:
        structure_score = max(structure_score, 100.0)
    elif breakout["breakout_20d"]:
        structure_score = max(structure_score, 92.0)
    if retest:
        structure_score = max(structure_score, 96.0)
    if failed_breakout:
        structure_score = min(structure_score, 20.0)

    volume_score, volume_acceleration, median_turnover = _volume_score(df, cfg, _finite(snap.get("daily_rvol")))
    entry_components["structure_breakout_retest"] = structure_score
    entry_components["weekly_alignment"] = weekly_score
    entry_components["volume_liquidity_confirmation"] = volume_score
    exit_components["failed_breakout_structure"] = 100.0 if failed_breakout else 15.0

    entry_score, entry_coverage = _weighted(entry_components, cfg["entry_weights"])
    exit_score, exit_coverage = _weighted(exit_components, cfg["exit_risk_weights"])

    trend_path = df["close"].diff().abs().rolling(20, min_periods=10).sum()
    trend_net = (df["close"] - df["close"].shift(20)).abs()
    trend_efficiency = _finite((trend_net / trend_path.replace(0, np.nan)).iloc[-1])

    current_volume_median = df["volume"].shift(1).rolling(20, min_periods=10).median()
    down_distribution = (
        (df["close"] < df["close"].shift(1))
        & (df["volume"] > current_volume_median)
    ).iloc[-3:]
    distribution_streak = int(down_distribution.fillna(False).sum())
    close_below_previous_low = bool(close < previous_low)
    rejection_on_volume = bool(
        (_finite(snap.get("upper_wick_ratio")) or 0.0) >= 0.35
        and (_finite(snap.get("daily_rvol")) or 0.0) >= float(th["daily_rvol_confirmation"])
        and (_finite(snap.get("close_location_value")) or 0.0) <= 0.0
    )

    invalidation_candidates = [
        x for x in (active_level, breakout["prior_low10"]) if x is not None and x < close
    ]
    invalidation = max(invalidation_candidates) if invalidation_candidates else breakout["prior_low10"]
    invalidation_distance = None if invalidation is None or close <= 0 else float(invalidation / close - 1.0)
    invalidation_too_wide = bool(
        invalidation_distance is not None
        and abs(min(invalidation_distance, 0.0))
        > float(th["structural_invalidation_distance_research_ceiling_pct"])
    )

    daily_rvol = _finite(snap.get("daily_rvol"))
    expansion = bool(snap.get("expansion_after_compression"))
    clv = _finite(snap.get("close_location_value"))
    confirmations = {
        "STRUCTURE": bool(breakout["breakout_20d"] or breakout["breakout_55d"] or retest),
        "RVOL": bool(daily_rvol is not None and daily_rvol >= float(th["daily_rvol_confirmation"])),
        "VOLUME_ACCELERATION": bool(
            volume_acceleration is not None and volume_acceleration >= float(th["volume_acceleration_confirmation"])
        ),
        "VOLATILITY_EXPANSION": expansion,
        "WEEKLY": bool(weekly_score is not None and weekly_score >= float(th["weekly_alignment_min"])),
        "PRICE_ACTION": bool(clv is not None and clv >= 0.40 and close >= open_),
    }
    confirmation_count = int(sum(confirmations.values()))
    trigger_confirmed = bool(confirmations["STRUCTURE"] or confirmations["RVOL"] or confirmations["VOLATILITY_EXPANSION"])
    weekly_adverse = bool(weekly_score is not None and weekly_score < float(th["weekly_adverse_max"]))
    entry_exit_conflict = bool(exit_score is not None and exit_score >= float(th["max_exit_risk_for_entry"]))
    liquidity_floor = float(th["minimum_median_turnover_eur_research"])
    low_liquidity = bool(median_turnover is not None and median_turnover < liquidity_floor)
    overextended = bool((_finite(snap.get("distance_rvwap20_atr")) or -999.0) >= float(th["overextension_atr"]))
    excessive_gap = bool((_finite(snap.get("gap_atr")) or -999.0) >= float(th["gap_excess_atr"]))

    if entry_score is None or entry_coverage < float(th["minimum_entry_coverage"]):
        entry_state = "DATA_INSUFFICIENT"
    elif low_liquidity:
        entry_state = "LIQUIDITY_WARNING_SHADOW"
    elif failed_breakout or entry_exit_conflict:
        entry_state = "ENTRY_CONFLICT_SHADOW"
    elif weekly_adverse:
        entry_state = "WEEKLY_CONFLICT_SHADOW"
    elif invalidation_too_wide:
        entry_state = "WAIT_RISK_SHADOW"
    elif overextended or excessive_gap:
        entry_state = "WAIT_PULLBACK_SHADOW"
    elif entry_score >= float(th["entry_strong"]) and confirmation_count >= int(th["entry_strong_min_confirmations"]) and trigger_confirmed:
        entry_state = "ENTRY_STRONG_SHADOW"
    elif entry_score >= float(th["entry_ready"]) and confirmation_count >= int(th["entry_ready_min_confirmations"]) and trigger_confirmed:
        entry_state = "ENTRY_READY_SHADOW"
    else:
        entry_state = "WAIT_SHADOW"

    structural_exit_confirmation = bool(
        failed_breakout
        or close_below_previous_low
        or distribution_streak >= int(th["distribution_streak_exit_confirmation"])
    )
    if exit_score is None:
        exit_state = "DATA_INSUFFICIENT"
    elif exit_score >= float(th["exit_risk_high"]) and structural_exit_confirmation:
        exit_state = "EXIT_RISK_HIGH_SHADOW"
    elif exit_score >= float(th["exit_watch"]) or close_below_previous_low or rejection_on_volume:
        exit_state = "EXIT_WATCH_SHADOW"
    else:
        exit_state = "HOLD_SUPPORTIVE_SHADOW"

    reasons = [x for x in str(snap.get("entry_reasons") or "").split("|") if x]
    if retest:
        reasons.append("BREAKOUT_RETEST")
    if confirmations["WEEKLY"]:
        reasons.append("WEEKLY_ALIGNMENT")
    if confirmations["PRICE_ACTION"]:
        reasons.append("STRONG_CLOSE")
    if trend_efficiency is not None and trend_efficiency >= 0.45:
        reasons.append("TREND_EFFICIENCY")

    warnings = [x for x in str(snap.get("warnings") or "").split("|") if x]
    if failed_breakout:
        warnings.append("FAILED_BREAKOUT")
    if close_below_previous_low:
        warnings.append("CLOSE_BELOW_PREVIOUS_LOW")
    if rejection_on_volume:
        warnings.append("UPPER_WICK_REJECTION_ON_VOLUME")
    if distribution_streak >= 2:
        warnings.append("MULTI_DAY_DISTRIBUTION")
    if weekly_adverse:
        warnings.append("WEEKLY_ADVERSE")
    if entry_exit_conflict:
        warnings.append("ENTRY_EXIT_SIGNAL_CONFLICT")
    if invalidation_too_wide:
        warnings.append("STRUCTURAL_INVALIDATION_DISTANCE_ABOVE_RESEARCH_CEILING")

    snap.update(
        {
            "entry_state": entry_state,
            "entry_score": None if entry_score is None else round(float(entry_score), 4),
            "entry_coverage": round(float(entry_coverage), 4),
            "entry_confirmation_count": confirmation_count,
            "entry_trigger_confirmed": trigger_confirmed,
            "exit_state": exit_state,
            "exit_risk_score": None if exit_score is None else round(float(exit_score), 4),
            "exit_coverage": round(float(exit_coverage), 4),
            "entry_components": entry_components,
            "exit_components": exit_components,
            "breakout_20d": breakout["breakout_20d"],
            "breakout_55d": breakout["breakout_55d"],
            "breakout_retest": retest,
            "active_breakout_level": active_level,
            "active_breakout_kind": active_kind,
            "active_breakout_age_sessions": active_age,
            "retest_level": breakout["recent_level"] if retest else None,
            "failed_breakout": failed_breakout,
            "volume_acceleration_5v20": volume_acceleration,
            "median_turnover_20d": median_turnover,
            "trend_efficiency_20d": trend_efficiency,
            "close_below_previous_low": close_below_previous_low,
            "upper_wick_rejection_on_volume": rejection_on_volume,
            "distribution_streak_3d": distribution_streak,
            "structural_invalidation_reference": invalidation,
            "structural_invalidation_distance_pct": invalidation_distance,
            "entry_reasons": _join_unique(reasons),
            "warnings": _join_unique(warnings),
            "intraday_data_used": False,
            "new_market_data_downloads_required": False,
            "decision_influence": 0.0,
            "score_influence": 0.0,
            "sizing_influence": 0.0,
            "stop_loss_influence": 0.0,
        }
    )
    return snap