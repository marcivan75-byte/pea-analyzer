from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


ENGINE_VERSION = "ACTION_MT_V1.0.0_SHADOW"


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _clip(value: float) -> float:
    return float(np.clip(value, 0.0, 100.0))


def _mean(values: list[float | None]) -> float | None:
    observed = [float(value) for value in values if value is not None]
    return float(np.mean(observed)) if observed else None


def _context_score(context: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = _finite(context.get(name))
        if value is not None and 0.0 <= value <= 100.0:
            return value
    return None


def _rsi(close: pd.Series, periods: int = 14) -> float | None:
    delta = close.diff().dropna()
    if len(delta) < periods:
        return None
    gain = delta.clip(lower=0).rolling(periods).mean().iloc[-1]
    loss = -delta.clip(upper=0).rolling(periods).mean().iloc[-1]
    if not np.isfinite(gain) or not np.isfinite(loss):
        return None
    if loss == 0:
        return 100.0
    return float(100.0 - 100.0 / (1.0 + gain / loss))


def _return(close: pd.Series, sessions: int) -> float | None:
    if len(close) <= sessions or close.iloc[-sessions - 1] <= 0:
        return None
    return float((close.iloc[-1] / close.iloc[-sessions - 1] - 1.0) * 100.0)


def _technical_components(frame: pd.DataFrame, cfg: dict[str, Any]) -> tuple[dict[str, float | None], dict[str, Any]]:
    close = pd.to_numeric(frame.get("close"), errors="coerce").dropna()
    volume = pd.to_numeric(frame.get("volume"), errors="coerce") if "volume" in frame else pd.Series(dtype=float)
    if close.empty:
        return {}, {}

    last = float(close.iloc[-1])
    sma50 = float(close.tail(50).mean()) if len(close) >= 50 else None
    sma100 = float(close.tail(100).mean()) if len(close) >= 100 else None
    sma200 = float(close.tail(200).mean()) if len(close) >= 200 else None
    ret21, ret42, ret63, ret126, ret189, ret252 = (_return(close, horizon) for horizon in (21, 42, 63, 126, 189, 252))

    trend_checks = [
        None if sma50 is None else 100.0 if last >= sma50 else 0.0,
        None if sma100 is None else 100.0 if last >= sma100 else 0.0,
        None if sma200 is None else 100.0 if last >= sma200 else 0.0,
        None if sma50 is None or sma200 is None else 100.0 if sma50 >= sma200 else 0.0,
    ]
    trend = _mean(trend_checks)
    momentum = _mean([
        None if ret63 is None else _clip(50.0 + 2.0 * ret63),
        None if ret126 is None else _clip(50.0 + 1.25 * ret126),
        None if ret252 is None else _clip(50.0 + 0.75 * ret252),
    ])

    returns = close.pct_change().dropna()
    vol = float(returns.tail(63).std(ddof=0) * np.sqrt(252.0) * 100.0) if len(returns) >= 40 else None
    downside = returns.tail(126)
    downside = downside[downside < 0]
    downside_vol = float(downside.std(ddof=0) * np.sqrt(252.0) * 100.0) if len(downside) >= 10 else None
    peak = close.tail(252).cummax()
    max_drawdown = float(((close.tail(252) / peak) - 1.0).min() * 100.0) if len(close) >= 63 else None
    risk = _mean([
        None if vol is None else _clip(100.0 - max(0.0, vol - 15.0) * 2.5),
        None if downside_vol is None else _clip(100.0 - max(0.0, downside_vol - 10.0) * 3.0),
        None if max_drawdown is None else _clip(100.0 + max_drawdown * 2.5),
    ])

    path63 = close.tail(64).diff().abs().sum() if len(close) >= 64 else np.nan
    efficiency63 = (
        abs(float(close.iloc[-1] - close.iloc[-64])) / float(path63)
        if len(close) >= 64 and np.isfinite(path63) and path63 > 0
        else None
    )
    efficiency = None if efficiency63 is None else _clip(efficiency63 * 160.0)
    r126 = returns.tail(126)
    std126 = float(r126.std(ddof=1)) if len(r126) >= 40 else None
    sharpe126 = float(r126.mean() / std126 * np.sqrt(252.0)) if std126 and std126 > 0 else None
    downside_rms = float(np.sqrt((r126[r126 < 0].pow(2)).mean()) * np.sqrt(252.0)) if (r126 < 0).any() else None
    sortino126 = float(r126.mean() * 252.0 / downside_rms) if downside_rms and downside_rms > 0 else None
    gains = float(r126[r126 > 0].sum())
    losses = abs(float(r126[r126 < 0].sum()))
    gain_to_pain = gains / losses if losses > 0 else None
    risk_adjusted = _mean([
        None if sharpe126 is None else _clip(50.0 + 25.0 * sharpe126),
        None if sortino126 is None else _clip(50.0 + 15.0 * sortino126),
        None if gain_to_pain is None else _clip(gain_to_pain * 50.0),
    ])

    median_turnover = None
    if not volume.empty and len(volume.dropna()) >= 20:
        aligned_close = pd.to_numeric(frame["close"], errors="coerce")
        median_turnover = float((aligned_close * volume).tail(20).median())
    floor = float(cfg["gates"]["minimum_median_turnover_eur"])
    preferred = float(cfg["gates"]["preferred_median_turnover_eur"])
    liquidity = None if median_turnover is None else _clip((median_turnover - floor) / max(preferred - floor, 1.0) * 100.0)

    rvol20 = None
    volume_trend = None
    if not volume.empty and len(volume.dropna()) >= 126:
        avg20 = float(volume.tail(20).mean())
        avg126 = float(volume.tail(126).mean())
        rvol20 = float(volume.iloc[-1] / avg20) if avg20 > 0 else None
        volume_trend = float(avg20 / avg126 - 1.0) if avg126 > 0 else None
    volume_confirmation = _mean([
        None if rvol20 is None else _clip(50.0 + (rvol20 - 1.0) * 80.0),
        None if volume_trend is None else _clip(50.0 + volume_trend * 250.0),
    ])

    rsi = _rsi(close)
    rsi_quality = None if rsi is None else _clip(100.0 - abs(rsi - 58.0) * 3.0)
    technical = _mean([trend, momentum, rsi_quality])
    return {
        "trend": trend,
        "momentum": momentum,
        "technical": technical,
        "risk": risk,
        "liquidity": liquidity,
        "efficiency": efficiency,
        "risk_adjusted": risk_adjusted,
        "volume_confirmation": volume_confirmation,
    }, {
        "reference_close": last,
        "return_1m_pct": ret21,
        "return_2m_pct": ret42,
        "return_3m_pct": ret63,
        "return_6m_pct": ret126,
        "return_9m_pct": ret189,
        "return_12m_pct": ret252,
        "sma50": sma50,
        "sma100": sma100,
        "sma200": sma200,
        "rsi14": rsi,
        "annualized_volatility_63d_pct": vol,
        "downside_volatility_126d_pct": downside_vol,
        "max_drawdown_252d_pct": max_drawdown,
        "median_turnover_20d_eur": median_turnover,
        "efficiency_63d": efficiency63,
        "sharpe_126d": sharpe126,
        "sortino_126d": sortino126,
        "gain_to_pain_126d": gain_to_pain,
        "relative_volume_20d": rvol20,
        "volume_trend_20_vs_126": volume_trend,
    }


def _weighted_score(components: dict[str, float | None], weights: dict[str, float]) -> tuple[float | None, float]:
    available = {name: value for name, value in components.items() if value is not None and name in weights}
    available_weight = sum(float(weights[name]) for name in available)
    coverage = available_weight / sum(float(value) for value in weights.values())
    if not available or available_weight <= 0:
        return None, 0.0
    score = sum(float(value) * float(weights[name]) for name, value in available.items()) / available_weight
    return _clip(score), float(coverage)


def compute_action_mt_snapshot(
    frame: pd.DataFrame,
    cfg: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute a medium-term Actions research snapshot without look-ahead or imputation."""
    context = context or {}
    minimum_history = int(cfg["data_policy"]["minimum_history_sessions"])
    if frame.empty or "close" not in frame or pd.to_numeric(frame["close"], errors="coerce").notna().sum() < minimum_history:
        return {"version_engine": ENGINE_VERSION, "status": "DATA_INSUFFICIENT", "decision": "DATA_INSUFFICIENT"}

    technical, diagnostics = _technical_components(frame, cfg)
    components: dict[str, float | None] = {
        "trend": technical.get("trend"),
        "momentum": technical.get("momentum"),
        "quality": _mean([
            _context_score(context, "quality_score", "morningstar_action_score"),
            _context_score(context, "profitability_score", "roe_score"),
            _context_score(context, "balance_sheet_score", "financial_strength_score"),
        ]),
        "growth": _mean([
            _context_score(context, "earnings_growth_score", "eps_growth_score"),
            _context_score(context, "revenue_growth_score"),
            _context_score(context, "free_cash_flow_growth_score"),
        ]),
        "valuation": _context_score(context, "valuation_discount_score", "valuation_score"),
        "revisions": _mean([
            _context_score(context, "analyst_revisions_score", "consensus_score_100_v21"),
            _context_score(context, "target_upside_growth_score"),
        ]),
        "sector_macro": _mean([
            _context_score(context, "sector_rotation_score"),
            _context_score(context, "sector_macro_score") if context.get("macro_evidence_sufficient") is not False else None,
            _context_score(context, "theme_risk_adjusted_score"),
        ]),
        "risk": technical.get("risk"),
        "liquidity": technical.get("liquidity"),
        "efficiency": technical.get("efficiency"),
        "risk_adjusted": technical.get("risk_adjusted"),
        "volume_confirmation": technical.get("volume_confirmation"),
    }
    score, coverage = _weighted_score(components, cfg["score_weights"])
    gates = cfg["gates"]
    warnings: list[str] = []
    mandatory = ("trend", "risk", "liquidity")
    missing_mandatory = [name for name in mandatory if components.get(name) is None]
    if missing_mandatory:
        warnings.append("MISSING_MANDATORY:" + ",".join(missing_mandatory))
    turnover = diagnostics.get("median_turnover_20d_eur")
    if turnover is not None and turnover < float(gates["minimum_median_turnover_eur"]):
        warnings.append("LIQUIDITY_BLOCK")
    max_drawdown = diagnostics.get("max_drawdown_252d_pct")
    if max_drawdown is not None and max_drawdown <= float(gates["maximum_drawdown_block_pct"]):
        warnings.append("DRAWDOWN_BLOCK")
    days_to_earnings = _finite(context.get("days_to_earnings"))
    if days_to_earnings is not None and 0 <= days_to_earnings <= float(gates["earnings_caution_days"]):
        warnings.append("EARNINGS_EVENT_CAUTION")

    market_regime = _context_score(context, "market_regime_score")
    sector = components.get("sector_macro")
    confirmations = {
        "TREND": bool(components["trend"] is not None and components["trend"] >= 75.0),
        "MOMENTUM": bool(components["momentum"] is not None and components["momentum"] >= 60.0),
        "QUALITY": bool(components["quality"] is not None and components["quality"] >= 60.0),
        "RISK_ADJUSTED": bool(components["risk_adjusted"] is not None and components["risk_adjusted"] >= 55.0),
        "SECTOR": bool(sector is not None and sector >= 55.0),
        "VOLUME": bool(components["volume_confirmation"] is not None and components["volume_confirmation"] >= 50.0),
    }
    confirmation_count = int(sum(confirmations.values()))
    valuation_context_conflict = bool(
        sector is not None and sector >= 70.0
        and components["valuation"] is not None and components["valuation"] <= 30.0
    )
    if valuation_context_conflict:
        warnings.append("HOT_SECTOR_OVERVALUATION")
    hard_block = bool("LIQUIDITY_BLOCK" in warnings or "DRAWDOWN_BLOCK" in warnings)
    if score is None or coverage < float(gates["minimum_score_coverage"]) or missing_mandatory:
        decision = "DATA_INSUFFICIENT"
    elif hard_block or (market_regime is not None and market_regime < float(gates["minimum_market_regime_score"])):
        decision = "RISK_BLOCKED_SHADOW"
    elif valuation_context_conflict:
        decision = "CONTEXT_CONFLICT_SHADOW"
    elif score >= float(gates["entry_strong_score"]) and confirmation_count >= int(gates["entry_strong_min_confirmations"]):
        decision = "ENTRY_STRONG_SHADOW"
    elif score >= float(gates["entry_ready_score"]) and confirmation_count >= int(gates["entry_ready_min_confirmations"]):
        decision = "ENTRY_READY_SHADOW"
    else:
        decision = "WATCH_SHADOW"

    return {
        "version_engine": ENGINE_VERSION,
        "status": "SUCCESS_SHADOW",
        "decision": decision,
        "score": score,
        "score_coverage": coverage,
        "components": components,
        "warnings": "|".join(warnings),
        "confirmation_count": confirmation_count,
        "confirmations": "|".join(name for name, passed in confirmations.items() if passed),
        **diagnostics,
        "real_orders_enabled": False,
        "structural_snapshot_can_promote_signal": False,
        "holdout_locked": True,
    }


@dataclass(frozen=True)
class PositionState:
    entry_price: float
    close: float
    holding_sessions: int
    peak_close: float


def exit_decision(position: PositionState, cfg: dict[str, Any]) -> str:
    """Close-only MT risk policy; no fixed profit target is used."""
    policy = cfg["exit_policy"]
    performance = position.close / position.entry_price - 1.0
    trailing_drawdown = position.close / position.peak_close - 1.0
    if performance <= float(policy["hard_stop_return"]):
        return "HARD_STOP_CLOSE_SHADOW"
    if position.holding_sessions >= int(policy["maximum_holding_sessions"]):
        return "TIME_REVIEW_CLOSE_SHADOW"
    if position.holding_sessions >= int(policy["trailing_stop_activation_sessions"]) and trailing_drawdown <= float(policy["trailing_stop_return"]):
        return "TRAILING_STOP_CLOSE_SHADOW"
    return "HOLD_SHADOW"

