from __future__ import annotations

import pandas as pd

from v182.features import tct_catalyst_context_v24_4 as base
from v182.sources.global_market_snapshot import GlobalMarketSnapshot
from v182.sources.tct_catalyst_news_v24_4_1 import CatalystNews


VERSION = "TCT_V24.4.1_NEXT_SESSION_CATALYST_CYCLE_SHADOW"

catalyst_window = base.catalyst_window
infer_phase = base.infer_phase
select_catalyst_candidates = base.select_catalyst_candidates


def _scheduled_event_proximity(row: pd.Series, cfg: dict) -> float | None:
    """Score only ex-ante scheduled events, not news already scored elsewhere.

    V24.4.0 allowed an existing news score to feed both the 45% news block and
    the 15% event-proximity block. V24.4.1 removes that double counting. The
    current scheduled event available in the daily seed is earnings proximity.
    """
    days = base._finite(row.get("days_to_earnings"))
    if days is None:
        return None
    horizon = max(float(cfg["thresholds"].get("earnings_proximity_days", 7)), 1.0)
    if days < 0 or days > horizon:
        return None
    return base._clip(100.0 - days / horizon * 55.0)


def score_candidate(
    row: pd.Series,
    news: CatalystNews | None,
    market: GlobalMarketSnapshot,
    *,
    phase: str,
    cfg: dict,
) -> dict:
    """Compute V24.4.1 scores with fail-closed evidence coverage.

    A missing dominant data source is never silently compensated by
    renormalising the remaining components into a high-confidence alert.
    Zero articles with a successful GDELT query remain valid observed evidence
    and therefore do not reduce coverage.
    """
    news_magnitude, news_direction = base._news_values(news)
    tech_magnitude, tech_direction = base._technical_impulse(row)
    event_proximity = _scheduled_event_proximity(row, cfg)
    global_shock = base._finite(market.shock_magnitude_score)
    risk_on = base._finite(market.risk_on_score)

    movement_components = {
        "news_magnitude": news_magnitude,
        "technical_impulse": tech_magnitude,
        "global_market_shock": global_shock,
        "known_event_proximity": event_proximity,
    }
    movement_raw, movement_coverage = base._weighted(movement_components, cfg["movement_potential_weights"])

    exit_risk = base._finite(row.get("exit_risk_score"))
    exit_inverse_direction = None if exit_risk is None else base._clip(50.0 - exit_risk, -50.0, 50.0) * 2.0
    global_direction = None if risk_on is None else base._clip((risk_on - 50.0) * 2.0, -100.0, 100.0)
    direction_components = {
        "news_direction": news_direction,
        "technical_direction": tech_direction,
        "exit_risk_inverse": exit_inverse_direction,
        "global_risk_on": global_direction,
    }
    numerator = 0.0
    observed = 0.0
    total_direction_weight = max(sum(float(v) for v in cfg["direction_weights"].values()), 1e-12)
    for key, weight in cfg["direction_weights"].items():
        value = base._finite(direction_components.get(key))
        if value is None:
            continue
        numerator += value * float(weight)
        observed += float(weight)
    direction_raw = None if observed <= 0 else base._clip(numerator / observed, -100.0, 100.0)
    direction_coverage = observed / total_direction_weight

    th = cfg["thresholds"]
    min_move_coverage = float(th.get("minimum_movement_coverage_for_scored_alert", 0.70))
    min_direction_coverage = float(th.get("minimum_direction_coverage_for_directional_alert", 0.70))
    movement = movement_raw if movement_coverage >= min_move_coverage else None
    direction = direction_raw if direction_coverage >= min_direction_coverage else None

    news_conflict = bool(
        news_direction is not None
        and tech_direction is not None
        and abs(news_direction) >= 35
        and abs(tech_direction) >= 35
        and news_direction * tech_direction < 0
    )
    movement_value = movement or 0.0
    direction_value = direction or 0.0
    news_error = None if news is None else news.error

    if movement is None:
        state = "DATA_DEGRADED_SHADOW"
    elif news_conflict and movement_value >= float(th["medium_movement_potential"]):
        state = "NEWS_CONFLICT_SHADOW"
    elif (
        movement_value >= float(th["high_movement_potential"])
        and direction is not None
        and direction_value >= float(th["bullish_direction"])
    ):
        state = "UP_CATALYST_SHADOW"
    elif (
        movement_value >= float(th["high_movement_potential"])
        and direction is not None
        and direction_value <= float(th["bearish_direction"])
    ):
        state = "DOWN_CATALYST_SHADOW"
    elif movement_value >= float(th["high_movement_potential"]):
        state = "VOLATILITY_ALERT_SHADOW"
    elif (news_magnitude or 0.0) <= 15 and (tech_magnitude or 0.0) >= float(th["medium_movement_potential"]):
        state = "TECHNICAL_ONLY_SHADOW"
    else:
        state = "NO_CATALYST_SHADOW"

    event_types = "" if news is None else "|".join(news.event_types)
    headlines = "" if news is None else " || ".join(news.top_headlines)
    data_quality_state = "COMPLETE_ENOUGH" if movement is not None else "INSUFFICIENT_MOVEMENT_COVERAGE"
    if movement is not None and direction is None:
        data_quality_state = "DIRECTION_COVERAGE_INSUFFICIENT"

    return {
        "version": VERSION,
        "phase": str(phase).upper(),
        "movement_potential_score": None if movement is None else round(float(movement), 4),
        "movement_potential_raw_score": None if movement_raw is None else round(float(movement_raw), 4),
        "movement_potential_coverage": round(float(movement_coverage), 4),
        "direction_bias_score": None if direction is None else round(float(direction), 4),
        "direction_bias_raw_score": None if direction_raw is None else round(float(direction_raw), 4),
        "direction_coverage": round(float(direction_coverage), 4),
        "data_quality_state": data_quality_state,
        "catalyst_state": state,
        "news_magnitude_score": news_magnitude,
        "news_direction_score": news_direction,
        "news_confidence": None if news is None else news.confidence,
        "news_article_count": 0 if news is None else news.article_count,
        "news_independent_sources": 0 if news is None else news.independent_sources,
        "news_event_types": event_types,
        "news_top_headlines": headlines,
        "news_window_start_utc": None if news is None else news.window_start_utc,
        "news_window_end_utc": None if news is None else news.window_end_utc,
        "news_error": news_error,
        "technical_impulse_score": tech_magnitude,
        "technical_direction_score": tech_direction,
        "known_event_proximity_score": event_proximity,
        "global_market_shock_score": global_shock,
        "global_risk_on_score": risk_on,
        "news_technical_conflict": news_conflict,
        "individual_extended_hours_quotes_used": False,
        "intraday_bars_used": False,
        "continuous_monitoring_used": False,
        "decision_influence": 0.0,
        "score_influence": 0.0,
        "sizing_influence": 0.0,
        "stop_loss_influence": 0.0,
        "ct_influence": 0.0,
    }
