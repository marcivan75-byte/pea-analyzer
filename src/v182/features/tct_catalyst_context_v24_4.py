from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from v182.sources.global_market_snapshot import GlobalMarketSnapshot
from v182.sources.tct_catalyst_news import CatalystNews


VERSION = "TCT_V24.4.0_NEXT_SESSION_CATALYST_CYCLE_SHADOW"


def _finite(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return float(np.clip(value, low, high))


def _weighted(values: dict[str, float | None], weights: dict[str, float], *, center_missing: bool = False) -> tuple[float | None, float]:
    numerator = 0.0
    observed = 0.0
    total = float(sum(float(v) for v in weights.values()))
    for key, weight in weights.items():
        value = _finite(values.get(key))
        if value is None:
            if center_missing:
                value = 50.0
            else:
                continue
        numerator += float(value) * float(weight)
        observed += float(weight)
    if observed <= 0 or total <= 0:
        return None, 0.0
    return numerator / observed, observed / total


def previous_business_day(day):
    current = day - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def catalyst_window(phase: str, now: datetime, cfg: dict) -> tuple[datetime, datetime]:
    """Return strict UTC event window for PREOPEN or POSTMARKET.

    PREOPEN starts at the previous business day's European close. POSTMARKET
    starts at the current business day's European close. This intentionally
    captures corporate releases after the cash session rather than intraday flow.
    """
    tz = ZoneInfo(str(cfg["data_policy"].get("timezone", "Europe/Paris")))
    local = now.astimezone(tz)
    close_t = time(
        int(cfg["data_policy"].get("europe_close_hour", 17)),
        int(cfg["data_policy"].get("europe_close_minute", 30)),
    )
    phase_u = str(phase).upper()
    if phase_u == "PREOPEN":
        start_day = previous_business_day(local.date())
    elif phase_u == "POSTMARKET":
        start_day = local.date()
        if start_day.weekday() >= 5:
            start_day = previous_business_day(start_day + timedelta(days=1))
    else:
        raise ValueError(f"Unsupported catalyst phase: {phase}")
    start_local = datetime.combine(start_day, close_t, tzinfo=tz)
    return start_local.astimezone(timezone.utc), now.astimezone(timezone.utc)


def infer_phase(now: datetime, cfg: dict) -> str:
    tz = ZoneInfo(str(cfg["data_policy"].get("timezone", "Europe/Paris")))
    local = now.astimezone(tz)
    # PREOPEN run is scheduled around 07:40/08:40 local depending DST;
    # POSTMARKET around 22:15/23:15 local. Manual runs are assigned to the
    # nearest meaningful snapshot rather than creating extra monitoring phases.
    if local.hour < 12:
        return "PREOPEN"
    return "POSTMARKET"


def _candidate_priority(row: pd.Series, cfg: dict) -> float:
    th = cfg["thresholds"]
    entry = _finite(row.get("entry_score")) or 0.0
    exit_risk = _finite(row.get("exit_risk_score")) or 0.0
    news = _finite(row.get("news_catalyst_score")) or _finite(row.get("funnel_instrument_news_score")) or 0.0
    atr_pct = _finite(row.get("atr14_pct")) or 0.0
    earnings = _finite(row.get("days_to_earnings"))
    earnings_score = 0.0
    if earnings is not None and 0 <= earnings <= float(th.get("earnings_proximity_days", 7)):
        earnings_score = 100.0 - earnings / max(float(th.get("earnings_proximity_days", 7)), 1.0) * 45.0
    volatility_score = min(100.0, atr_pct / max(float(th.get("high_atr_pct", 0.04)), 1e-6) * 70.0)
    return 0.30 * entry + 0.25 * exit_risk + 0.20 * news + 0.15 * earnings_score + 0.10 * volatility_score


def select_catalyst_candidates(seed: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    if seed is None or seed.empty:
        return pd.DataFrame()
    work = seed.copy()
    work["_catalyst_priority"] = work.apply(lambda row: _candidate_priority(row, cfg), axis=1)
    limit = int(cfg["data_policy"].get("candidate_limit", 60))
    return work.sort_values("_catalyst_priority", ascending=False).head(limit).reset_index(drop=True)


def _technical_impulse(row: pd.Series) -> tuple[float | None, float | None]:
    entry = _finite(row.get("entry_score"))
    exit_risk = _finite(row.get("exit_risk_score"))
    atr_pct = _finite(row.get("atr14_pct"))
    range_expansion = _finite(row.get("range_expansion"))
    confirmations = _finite(row.get("entry_confirmation_count"))

    magnitude_values: list[float] = []
    if entry is not None:
        magnitude_values.append(abs(entry - 50.0) * 1.5)
    if exit_risk is not None:
        magnitude_values.append(exit_risk)
    if atr_pct is not None:
        magnitude_values.append(_clip(atr_pct / 0.05 * 100.0))
    if range_expansion is not None:
        magnitude_values.append(_clip((range_expansion - 0.7) / 1.3 * 100.0))
    if confirmations is not None:
        magnitude_values.append(_clip(confirmations / 4.0 * 100.0))
    magnitude = float(np.mean(sorted(magnitude_values, reverse=True)[:3])) if magnitude_values else None

    direction_parts: list[float] = []
    if entry is not None:
        direction_parts.append(_clip((entry - 50.0) * 2.0, -100.0, 100.0))
    if exit_risk is not None:
        direction_parts.append(-_clip(exit_risk, 0.0, 100.0))
    if str(row.get("entry_state") or "").startswith("ENTRY_"):
        direction_parts.append(60.0)
    if str(row.get("exit_state") or "") == "EXIT_RISK_HIGH_SHADOW":
        direction_parts.append(-80.0)
    direction = float(np.mean(direction_parts)) if direction_parts else None
    return magnitude, direction


def _event_proximity(row: pd.Series, cfg: dict) -> float | None:
    days = _finite(row.get("days_to_earnings"))
    existing_news = _finite(row.get("news_catalyst_score")) or _finite(row.get("funnel_instrument_news_score"))
    values: list[float] = []
    horizon = max(float(cfg["thresholds"].get("earnings_proximity_days", 7)), 1.0)
    if days is not None and 0 <= days <= horizon:
        values.append(_clip(100.0 - days / horizon * 55.0))
    if existing_news is not None:
        values.append(_clip(existing_news))
    return max(values) if values else None


def _news_values(news: CatalystNews | None) -> tuple[float | None, float | None]:
    if news is None:
        return None, None
    if news.article_count == 0 and not news.error:
        return 0.0, 0.0
    return _finite(news.magnitude_score), _finite(news.direction_score)


def score_candidate(
    row: pd.Series,
    news: CatalystNews | None,
    market: GlobalMarketSnapshot,
    *,
    phase: str,
    cfg: dict,
) -> dict:
    news_magnitude, news_direction = _news_values(news)
    tech_magnitude, tech_direction = _technical_impulse(row)
    event_proximity = _event_proximity(row, cfg)
    global_shock = _finite(market.shock_magnitude_score)
    risk_on = _finite(market.risk_on_score)

    movement_components = {
        "news_magnitude": news_magnitude,
        "technical_impulse": tech_magnitude,
        "global_market_shock": global_shock,
        "known_event_proximity": event_proximity,
    }
    movement, movement_coverage = _weighted(movement_components, cfg["movement_potential_weights"])

    exit_risk = _finite(row.get("exit_risk_score"))
    exit_inverse_direction = None if exit_risk is None else _clip(50.0 - exit_risk, -50.0, 50.0) * 2.0
    global_direction = None if risk_on is None else _clip((risk_on - 50.0) * 2.0, -100.0, 100.0)
    direction_components = {
        "news_direction": news_direction,
        "technical_direction": tech_direction,
        "exit_risk_inverse": exit_inverse_direction,
        "global_risk_on": global_direction,
    }
    # Direction components live on -100..100; reweight only observed evidence.
    numerator = 0.0
    observed = 0.0
    for key, weight in cfg["direction_weights"].items():
        value = _finite(direction_components.get(key))
        if value is None:
            continue
        numerator += value * float(weight)
        observed += float(weight)
    direction = None if observed <= 0 else _clip(numerator / observed, -100.0, 100.0)
    direction_coverage = observed / max(sum(float(v) for v in cfg["direction_weights"].values()), 1e-12)

    th = cfg["thresholds"]
    news_conflict = bool(
        news_direction is not None
        and tech_direction is not None
        and abs(news_direction) >= 35
        and abs(tech_direction) >= 35
        and news_direction * tech_direction < 0
    )
    movement_value = movement or 0.0
    direction_value = direction or 0.0
    if news_conflict and movement_value >= float(th["medium_movement_potential"]):
        state = "NEWS_CONFLICT_SHADOW"
    elif movement_value >= float(th["high_movement_potential"]) and direction_value >= float(th["bullish_direction"]):
        state = "UP_CATALYST_SHADOW"
    elif movement_value >= float(th["high_movement_potential"]) and direction_value <= float(th["bearish_direction"]):
        state = "DOWN_CATALYST_SHADOW"
    elif movement_value >= float(th["high_movement_potential"]):
        state = "VOLATILITY_ALERT_SHADOW"
    elif (news_magnitude or 0.0) <= 15 and (tech_magnitude or 0.0) >= float(th["medium_movement_potential"]):
        state = "TECHNICAL_ONLY_SHADOW"
    else:
        state = "NO_CATALYST_SHADOW"

    event_types = "" if news is None else "|".join(news.event_types)
    headlines = "" if news is None else " || ".join(news.top_headlines)
    return {
        "version": VERSION,
        "phase": str(phase).upper(),
        "movement_potential_score": None if movement is None else round(float(movement), 4),
        "movement_potential_coverage": round(float(movement_coverage), 4),
        "direction_bias_score": None if direction is None else round(float(direction), 4),
        "direction_coverage": round(float(direction_coverage), 4),
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
        "news_error": None if news is None else news.error,
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
