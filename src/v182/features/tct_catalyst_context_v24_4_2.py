from __future__ import annotations

import pandas as pd

from v182.features import tct_catalyst_context_v24_4 as base
from v182.sources.global_market_snapshot import GlobalMarketSnapshot
from v182.sources.tct_catalyst_news_v24_4_2 import CatalystNews


VERSION = "TCT_V24.4.2_NEXT_SESSION_CATALYST_CYCLE_SHADOW"
catalyst_window = base.catalyst_window
infer_phase = base.infer_phase


def _scheduled_event_proximity(row: pd.Series, cfg: dict) -> float | None:
    days = base._finite(row.get("days_to_earnings"))
    if days is None:
        return None
    horizon = max(float(cfg["thresholds"].get("earnings_proximity_days", 7)), 1.0)
    if days < 0 or days > horizon:
        return None
    return base._clip(100.0 - days / horizon * 55.0)


def _known_news(row: pd.Series) -> float:
    values = [
        base._finite(row.get("news_catalyst_score")),
        base._finite(row.get("funnel_instrument_news_score")),
    ]
    finite = [x for x in values if x is not None]
    return max(finite) if finite else 0.0


def _t1_t2_quality(row: pd.Series) -> float:
    values = [base._finite(row.get("source_t1_quality")), base._finite(row.get("source_t2_quality"))]
    finite = [x for x in values if x is not None]
    return max(finite) if finite else 0.0


def _priority_axes(work: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = work.copy()
    horizon = max(float(cfg["thresholds"].get("earnings_proximity_days", 7)), 1.0)
    high_atr = max(float(cfg["thresholds"].get("high_atr_pct", 0.04)), 1e-6)
    out["_entry_axis"] = pd.to_numeric(out.get("entry_score"), errors="coerce").fillna(0.0).clip(0, 100)
    out["_exit_axis"] = pd.to_numeric(out.get("exit_risk_score"), errors="coerce").fillna(0.0).clip(0, 100)
    out["_news_axis"] = out.apply(_known_news, axis=1)
    earnings = pd.to_numeric(out.get("days_to_earnings"), errors="coerce")
    out["_earnings_axis"] = ((1.0 - earnings.clip(lower=0, upper=horizon) / horizon) * 100.0).where(earnings.between(0, horizon), 0.0)
    atr = pd.to_numeric(out.get("atr14_pct"), errors="coerce").fillna(0.0)
    out["_volatility_axis"] = (atr / high_atr * 70.0).clip(0, 100)
    out["_t1t2_axis"] = out.apply(_t1_t2_quality, axis=1)
    weights = cfg.get("candidate_selection", {}).get("priority_weights", {})
    out["candidate_priority_score"] = (
        out["_entry_axis"] * float(weights.get("entry", 0.25))
        + out["_exit_axis"] * float(weights.get("exit_risk", 0.20))
        + out["_news_axis"] * float(weights.get("existing_news", 0.20))
        + out["_earnings_axis"] * float(weights.get("earnings_proximity", 0.15))
        + out["_volatility_axis"] * float(weights.get("volatility", 0.10))
        + out["_t1t2_axis"] * float(weights.get("t1_t2_quality", 0.10))
    )
    return out


def _reason(row: pd.Series, cfg: dict) -> str:
    reasons: list[str] = []
    state = str(row.get("entry_state") or "")
    if state in {"ENTRY_READY_SHADOW", "ENTRY_STRONG_SHADOW"}:
        reasons.append("ENTRY_READY_OR_STRONG")
    days = base._finite(row.get("days_to_earnings"))
    if days is not None and 0 <= days <= float(cfg["thresholds"].get("earnings_proximity_days", 7)):
        reasons.append("EARNINGS_WITHIN_7D")
    if _known_news(row) >= float(cfg["thresholds"].get("high_existing_news_catalyst", 65)):
        reasons.append("EXISTING_NEWS_HIGH")
    atr = base._finite(row.get("atr14_pct"))
    if atr is not None and atr >= float(cfg["thresholds"].get("high_atr_pct", 0.04)):
        reasons.append("HIGH_ATR")
    exit_risk = base._finite(row.get("exit_risk_score"))
    if exit_risk is not None and exit_risk >= float(cfg["thresholds"].get("high_exit_risk", 50)):
        reasons.append("HIGH_EXIT_RISK")
    return "|".join(reasons) if reasons else "COMPOSITE_FILL"


def select_catalyst_candidates(seed: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Quota-based diversified selection before bounded news retrieval."""
    if seed is None or seed.empty:
        return pd.DataFrame()
    work = _priority_axes(seed, cfg)
    work["candidate_rank_reason"] = work.apply(lambda row: _reason(row, cfg), axis=1)
    limit = int(cfg["data_policy"].get("candidate_limit", 60))
    quotas = cfg.get("candidate_selection", {}).get("quotas", {})
    selected: list[int] = []

    def take(mask: pd.Series, n: int, sort_column: str, ascending: bool = False) -> None:
        if n <= 0:
            return
        pool = work.loc[mask & ~work.index.isin(selected)].sort_values(sort_column, ascending=ascending)
        selected.extend(pool.head(int(n)).index.tolist())

    state = work.get("entry_state", pd.Series(index=work.index, dtype=object)).astype(str)
    take(state.isin({"ENTRY_READY_SHADOW", "ENTRY_STRONG_SHADOW"}), int(quotas.get("ENTRY_READY_OR_STRONG", 0)), "_entry_axis")
    earnings = pd.to_numeric(work.get("days_to_earnings"), errors="coerce")
    take(earnings.between(0, float(cfg["thresholds"].get("earnings_proximity_days", 7))), int(quotas.get("EARNINGS_WITHIN_7D", 0)), "_earnings_axis")
    take(work["_news_axis"] >= float(cfg["thresholds"].get("high_existing_news_catalyst", 65)), int(quotas.get("EXISTING_NEWS_HIGH", 0)), "_news_axis")
    take(work["_volatility_axis"] >= 70.0, int(quotas.get("HIGH_ATR", 0)), "_volatility_axis")
    take(work["_exit_axis"] >= float(cfg["thresholds"].get("high_exit_risk", 50)), int(quotas.get("HIGH_EXIT_RISK", 0)), "_exit_axis")

    if len(selected) < limit:
        remaining = work.loc[~work.index.isin(selected)].sort_values("candidate_priority_score", ascending=False)
        selected.extend(remaining.head(limit - len(selected)).index.tolist())
    result = work.loc[selected[:limit]].copy()
    result = result.sort_values("candidate_priority_score", ascending=False).reset_index(drop=True)
    result["candidate_rank"] = range(1, len(result) + 1)
    return result.drop(columns=[c for c in result.columns if c.startswith("_")], errors="ignore")


def _news_values(news: CatalystNews | None) -> tuple[float | None, float | None]:
    if news is None:
        return None, None
    if news.article_count == 0 and not news.error:
        return 0.0, 0.0
    return base._finite(news.magnitude_score), base._finite(news.direction_score)


def score_candidate(row: pd.Series, news: CatalystNews | None, market: GlobalMarketSnapshot, *, phase: str, cfg: dict) -> dict:
    news_magnitude, news_direction = _news_values(news)
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
    exit_inverse = None if exit_risk is None else base._clip(50.0 - exit_risk, -50.0, 50.0) * 2.0
    global_direction = None if risk_on is None else base._clip((risk_on - 50.0) * 2.0, -100.0, 100.0)
    direction_components = {
        "news_direction": news_direction,
        "technical_direction": tech_direction,
        "exit_risk_inverse": exit_inverse,
        "global_risk_on": global_direction,
    }
    numerator = 0.0
    observed = 0.0
    total = max(sum(float(v) for v in cfg["direction_weights"].values()), 1e-12)
    for key, weight in cfg["direction_weights"].items():
        value = base._finite(direction_components.get(key))
        if value is not None:
            numerator += value * float(weight)
            observed += float(weight)
    direction_raw = None if observed <= 0 else base._clip(numerator / observed, -100.0, 100.0)
    direction_coverage = observed / total

    th = cfg["thresholds"]
    movement = movement_raw if movement_coverage >= float(th.get("minimum_movement_coverage_for_scored_alert", 0.70)) else None
    direction = direction_raw if direction_coverage >= float(th.get("minimum_direction_coverage_for_directional_alert", 0.70)) else None
    news_conflict = bool(
        news_direction is not None and tech_direction is not None
        and abs(news_direction) >= 35 and abs(tech_direction) >= 35
        and news_direction * tech_direction < 0
    )
    movement_value = 0.0 if movement is None else float(movement)
    direction_value = 0.0 if direction is None else float(direction)
    if movement is None:
        state = "DATA_DEGRADED_SHADOW"
    elif news_conflict and movement_value >= float(th["medium_movement_potential"]):
        state = "NEWS_CONFLICT_SHADOW"
    elif movement_value >= float(th["high_movement_potential"]) and direction is not None and direction_value >= float(th["bullish_direction"]):
        state = "UP_CATALYST_SHADOW"
    elif movement_value >= float(th["high_movement_potential"]) and direction is not None and direction_value <= float(th["bearish_direction"]):
        state = "DOWN_CATALYST_SHADOW"
    elif movement_value >= float(th["high_movement_potential"]):
        state = "VOLATILITY_ALERT_SHADOW"
    elif (news_magnitude or 0.0) <= 15 and (tech_magnitude or 0.0) >= float(th["medium_movement_potential"]):
        state = "TECHNICAL_ONLY_SHADOW"
    else:
        state = "NO_CATALYST_SHADOW"

    data_quality = "COMPLETE_ENOUGH" if movement is not None else "INSUFFICIENT_MOVEMENT_COVERAGE"
    if movement is not None and direction is None:
        data_quality = "DIRECTION_COVERAGE_INSUFFICIENT"
    atr = base._finite(row.get("atr14_pct"))
    technical_only_actionable = bool(
        state == "TECHNICAL_ONLY_SHADOW"
        and (tech_magnitude or 0.0) >= float(th.get("technical_only_actionable", 70))
        and atr is not None and atr >= float(th.get("high_atr_pct", 0.04))
    )
    return {
        "version": VERSION,
        "phase": str(phase).upper(),
        "movement_potential_score": None if movement is None else round(float(movement), 4),
        "movement_potential_raw_score": None if movement_raw is None else round(float(movement_raw), 4),
        "movement_potential_coverage": round(float(movement_coverage), 4),
        "direction_bias_score": None if direction is None else round(float(direction), 4),
        "direction_bias_raw_score": None if direction_raw is None else round(float(direction_raw), 4),
        "direction_coverage": round(float(direction_coverage), 4),
        "data_quality_state": data_quality,
        "catalyst_state": state,
        "technical_only_actionable_flag": technical_only_actionable,
        "news_magnitude_score": news_magnitude,
        "news_direction_score": news_direction,
        "news_confidence": None if news is None else news.confidence,
        "news_match_confidence": None if news is None else news.match_confidence,
        "news_article_count": 0 if news is None else news.article_count,
        "news_independent_sources": 0 if news is None else news.independent_sources,
        "news_event_types": "" if news is None else "|".join(news.event_types),
        "news_top_headlines": "" if news is None else " || ".join(news.top_headlines),
        "news_window_start_utc": None if news is None else news.window_start_utc,
        "news_window_end_utc": None if news is None else news.window_end_utc,
        "news_source": None if news is None else news.source,
        "news_cache_hit": False if news is None else news.cache_hit,
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
