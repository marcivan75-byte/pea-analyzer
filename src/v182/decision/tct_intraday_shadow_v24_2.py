from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class IntradayShadowResult:
    session_date: str
    status: str
    shadow_state: str
    setup: str | None
    signal_time: str | None
    score: float | None
    coverage: float
    components: dict[str, float | None]
    entry_price: float | None
    structural_invalidation_reference: float | None
    structural_distance_pct: float | None
    mfe_to_close_pct: float | None
    mae_to_close_pct: float | None
    close_return_pct: float | None
    rejection_reason: str | None
    best_observed_score: float | None


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


def _weighted_score(components: dict[str, float | None], weights: dict[str, float]) -> tuple[float | None, float]:
    numer = 0.0
    observed = 0.0
    total = float(sum(weights.values()))
    for name, weight in weights.items():
        value = _finite(components.get(name))
        if value is None:
            continue
        numer += _clip(value) * float(weight)
        observed += float(weight)
    if observed <= 0 or total <= 0:
        return None, 0.0
    return _clip(numer / observed), float(np.clip(observed / total, 0.0, 1.0))


def _setup_at(session: pd.DataFrame, pos: int, cfg: dict) -> tuple[str | None, float | None]:
    row = session.iloc[pos]
    enabled = cfg.get("setups", {})
    candidates: list[tuple[str, float, float | None]] = []

    prior_high = _finite(row.get("prior_high"))
    if enabled.get("EXPLOSIVE_BREAKOUT", {}).get("enabled", False) and bool(row.get("intraday_breakout", False)):
        candidates.append(("EXPLOSIVE_BREAKOUT", 95.0, prior_high))

    if enabled.get("OPENING_RANGE_BREAKOUT", {}).get("enabled", False) and bool(row.get("opening_range_breakout", False)):
        candidates.append(("OPENING_RANGE_BREAKOUT", 90.0, _finite(row.get("opening_range_high"))))

    if enabled.get("VWAP_RECLAIM", {}).get("enabled", False) and bool(row.get("vwap_reclaim", False)):
        candidates.append(("VWAP_RECLAIM", 85.0, _finite(row.get("vwap"))))

    if enabled.get("BREAKOUT_RETEST", {}).get("enabled", False) and pos > 0:
        lookback = int(cfg["intraday_data"].get("retest_lookback_bars", 6))
        prior = session.iloc[max(0, pos - lookback):pos]
        breakout_rows = prior[prior["intraday_breakout"].fillna(False).astype(bool)]
        if not breakout_rows.empty:
            source = breakout_rows.iloc[-1]
            level = _finite(source.get("prior_high"))
            low = _finite(row.get("low"))
            close = _finite(row.get("close"))
            if level is not None and low is not None and close is not None and low <= level * 1.003 and close >= level:
                candidates.append(("BREAKOUT_RETEST", 100.0, level))

    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates[0][0], candidates[0][2]


def _components(row: pd.Series, setup: str | None, structure_score: float | None, cfg: dict) -> dict[str, float | None]:
    th = cfg["shadow_thresholds"]
    rvol = _finite(row.get("rvol_slot"))
    vol_acc = _finite(row.get("volume_acceleration"))
    rvol_score = _scale(rvol, 0.8, 2.0)
    vol_acc_score = _scale(vol_acc, 0.8, 2.0)
    volume_values = [x for x in (rvol_score, vol_acc_score) if x is not None]
    volume_score = float(np.mean(volume_values)) if volume_values else None

    dist = _finite(row.get("vwap_distance_pct"))
    slope = _finite(row.get("vwap_slope_3"))
    vwap_score = None
    close = _finite(row.get("close"))
    vwap = _finite(row.get("vwap"))
    if dist is not None and close is not None and vwap is not None:
        max_ext = float(th["max_vwap_extension_pct"])
        if dist < 0:
            positional = _clip(40.0 + dist / max(max_ext, 1e-6) * 40.0)
        elif dist <= max_ext:
            positional = _clip(85.0 + min(dist / max(max_ext, 1e-6), 1.0) * 15.0)
        else:
            positional = _clip(100.0 - (dist - max_ext) / max(max_ext, 1e-6) * 80.0)
        slope_score = 50.0 if slope is None else _clip(50.0 + slope * 5000.0)
        vwap_score = 0.75 * positional + 0.25 * slope_score

    expansion = _finite(row.get("range_expansion_ratio"))
    atr_pct = _finite(row.get("intraday_atr_pct"))
    volatility_score = None
    if expansion is not None:
        expansion_score = _scale(expansion, 0.7, 2.0)
        if atr_pct is None:
            volatility_score = expansion_score
        else:
            atr_score = _clip(100.0 - abs(atr_pct - 0.006) / 0.018 * 100.0)
            volatility_score = 0.75 * float(expansion_score) + 0.25 * atr_score

    turnover_ratio = _finite(row.get("turnover_ratio"))
    spread_pct = _finite(row.get("spread_pct"))
    liquidity_values: list[float] = []
    if turnover_ratio is not None:
        turnover_score = _scale(turnover_ratio, 0.5, 2.0)
        if turnover_score is not None:
            liquidity_values.append(turnover_score)
    if spread_pct is not None:
        max_spread = float(th["max_spread_pct_if_available"])
        liquidity_values.append(_clip(100.0 - spread_pct / max(max_spread, 1e-8) * 100.0))
    liquidity_score = float(np.mean(liquidity_values)) if liquidity_values else None

    ret3 = _finite(row.get("return_3bar"))
    ema_slope = _finite(row.get("ema9_slope_3"))
    momentum_values: list[float] = []
    if ret3 is not None:
        momentum_values.append(_clip(50.0 + ret3 * 5000.0))
    if ema_slope is not None:
        momentum_values.append(_clip(50.0 + ema_slope * 5000.0))
    momentum_score = float(np.mean(momentum_values)) if momentum_values else None

    imbalance = _finite(row.get("order_flow_imbalance"))
    order_flow = None if imbalance is None else _clip(50.0 + imbalance * 100.0)

    return {
        "rvol_volume_acceleration": volume_score,
        "vwap_timing": vwap_score,
        "structure": structure_score if setup else None,
        "intraday_volatility": volatility_score,
        "liquidity_execution": liquidity_score,
        "momentum_5m": momentum_score,
        "order_flow_optional": order_flow,
    }


def _shadow_state(row: pd.Series, setup: str | None, score: float | None, coverage: float, cfg: dict) -> tuple[str, str | None]:
    th = cfg["shadow_thresholds"]
    if setup is None:
        return "WAIT_SHADOW", "NO_VALIDATED_INTRADAY_SETUP"
    if score is None or coverage < float(th["minimum_weighted_coverage"]):
        return "DATA_INSUFFICIENT", "WEIGHTED_COVERAGE_BELOW_THRESHOLD"

    close = _finite(row.get("close"))
    vwap = _finite(row.get("vwap"))
    if close is None or vwap is None or close <= vwap:
        return "WAIT_SHADOW", "VWAP_NOT_CONFIRMED"

    rvol = _finite(row.get("rvol_slot"))
    vol_acc = _finite(row.get("volume_acceleration"))
    volume_confirmed = (rvol is not None and rvol >= float(th["rvol_confirmation_min"])) or (
        rvol is None and vol_acc is not None and vol_acc >= 1.25
    )
    if not volume_confirmed:
        return "WAIT_SHADOW", "VOLUME_NOT_CONFIRMED"

    expansion = _finite(row.get("range_expansion_ratio"))
    if expansion is not None and expansion < float(th["range_expansion_min"]):
        return "WAIT_SHADOW", "RANGE_NOT_EXPANDING"

    turnover_ratio = _finite(row.get("turnover_ratio"))
    if turnover_ratio is not None and turnover_ratio < float(th["minimum_turnover_ratio"]):
        return "WAIT_SHADOW", "LIQUIDITY_RELATIVE_TOO_LOW"

    spread = _finite(row.get("spread_pct"))
    if spread is not None and spread > float(th["max_spread_pct_if_available"]):
        return "AVOID_SHADOW", "SPREAD_TOO_WIDE"

    if score >= float(th["entry_strong_score"]):
        return "ENTRY_STRONG_SHADOW", None
    if score >= float(th["entry_ready_score"]):
        return "ENTRY_READY_SHADOW", None
    return "WAIT_SHADOW", "DIAGNOSTIC_SCORE_BELOW_ENTRY_THRESHOLD"


def _post_entry_outcomes(session: pd.DataFrame, pos: int, entry: float) -> tuple[float | None, float | None, float | None]:
    """Label only price action observable after the entry-bar close.

    The SHADOW entry price is the close of bar ``pos``. High/low values from
    that same bar happened before the entry decision and must never contribute
    to post-entry MFE/MAE. Starting at ``pos + 1`` keeps outcome labelling
    causal and avoids a subtle performance bias.
    """
    if entry <= 0 or pos + 1 >= len(session):
        return None, None, None
    future = session.iloc[pos + 1:]
    high = pd.to_numeric(future["high"], errors="coerce").max()
    low = pd.to_numeric(future["low"], errors="coerce").min()
    close = _finite(future["close"].iloc[-1])
    mfe = None if pd.isna(high) else float(high / entry - 1.0)
    mae = None if pd.isna(low) else float(low / entry - 1.0)
    end = None if close is None else float(close / entry - 1.0)
    return mfe, mae, end


def evaluate_intraday_session(features: pd.DataFrame, session_date: str, cfg: dict) -> IntradayShadowResult:
    """Evaluate one session using only causal information at each candidate bar.

    Future bars are used only after a causal entry event to calculate labelled
    research outcomes (MFE/MAE/close return); they never affect entry selection.
    """
    if features is None or features.empty or "session_date" not in features.columns:
        return IntradayShadowResult(session_date, "NO_DATA", "DATA_INSUFFICIENT", None, None, None, 0.0, {}, None, None, None, None, None, None, "NO_INTRADAY_DATA", None)
    session = features[features["session_date"].astype(str) == str(session_date)].copy()
    minimum = int(cfg["intraday_data"]["minimum_session_bars"])
    if len(session) < minimum:
        return IntradayShadowResult(session_date, "SESSION_INCOMPLETE", "DATA_INSUFFICIENT", None, None, None, 0.0, {}, None, None, None, None, None, None, "SESSION_TOO_SHORT", None)

    weights = cfg["diagnostic_weights"]
    first_ready = None
    best_score: float | None = None
    last_eval = None

    for pos in range(len(session)):
        row = session.iloc[pos]
        setup, level = _setup_at(session, pos, cfg)
        structure_map = {
            "BREAKOUT_RETEST": 100.0,
            "EXPLOSIVE_BREAKOUT": 95.0,
            "OPENING_RANGE_BREAKOUT": 90.0,
            "VWAP_RECLAIM": 85.0,
        }
        components = _components(row, setup, structure_map.get(setup), cfg)
        score, coverage = _weighted_score(components, weights)
        state, reason = _shadow_state(row, setup, score, coverage, cfg)
        if score is not None and (best_score is None or score > best_score):
            best_score = score
        last_eval = (pos, row, setup, level, score, coverage, components, state, reason)
        if state in {"ENTRY_READY_SHADOW", "ENTRY_STRONG_SHADOW"}:
            first_ready = last_eval
            break

    chosen = first_ready or last_eval
    if chosen is None:
        return IntradayShadowResult(session_date, "NO_EVALUABLE_BAR", "DATA_INSUFFICIENT", None, None, None, 0.0, {}, None, None, None, None, None, None, "NO_EVALUABLE_BAR", best_score)

    pos, row, setup, level, score, coverage, components, state, reason = chosen
    entry = _finite(row.get("close")) if first_ready is not None else None
    invalidation = level if first_ready is not None else None
    if first_ready is not None and invalidation is None and setup == "VWAP_RECLAIM":
        invalidation = _finite(row.get("vwap"))
    distance = None
    if entry is not None and invalidation is not None and entry > 0:
        distance = float(invalidation / entry - 1.0)

    mfe = mae = close_return = None
    if entry is not None:
        mfe, mae, close_return = _post_entry_outcomes(session, pos, entry)

    signal_time = pd.Timestamp(session.index[pos]).isoformat()
    status = "CAUSAL_ENTRY_EVENT" if first_ready is not None else "NO_ENTRY_EVENT"
    return IntradayShadowResult(
        session_date=str(session_date),
        status=status,
        shadow_state=state,
        setup=setup,
        signal_time=signal_time,
        score=None if score is None else round(float(score), 4),
        coverage=round(float(coverage), 4),
        components=components,
        entry_price=entry,
        structural_invalidation_reference=invalidation,
        structural_distance_pct=distance,
        mfe_to_close_pct=mfe,
        mae_to_close_pct=mae,
        close_return_pct=close_return,
        rejection_reason=reason,
        best_observed_score=None if best_score is None else round(float(best_score), 4),
    )
