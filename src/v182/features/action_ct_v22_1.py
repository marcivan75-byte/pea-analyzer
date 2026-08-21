from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from v182.features.action_ct_v22_0 import compute_action_ct_snapshot as compute_v22_0
from v182.features.ct_math import clip_score, finite, mean_available, truthy, weighted_score


ENGINE_VERSION = "ACTION_CT_V22.1.0_CONTEXT_ENRICHED_SHADOW"


def _context_score(context: dict, key: str) -> float | None:
    value = finite(context.get(key))
    if value is None or not 0.0 <= value <= 100.0:
        return None
    return value


def _asymmetric_risk(frame: pd.DataFrame, cfg: dict) -> dict[str, float | None]:
    """Research-only downside asymmetry diagnostics over the latest 20 sessions."""
    if frame.empty or "close" not in frame.columns:
        return {
            "drawdown_20d_pct": None,
            "gain_loss_ratio_20d": None,
            "downside_volatility_20d_pct": None,
            "asymmetric_risk_score": None,
        }
    close = pd.to_numeric(frame["close"], errors="coerce").dropna().tail(21)
    if len(close) < 10:
        return {
            "drawdown_20d_pct": None,
            "gain_loss_ratio_20d": None,
            "downside_volatility_20d_pct": None,
            "asymmetric_risk_score": None,
        }
    returns = close.pct_change().dropna()
    recent = close.tail(20)
    peak = float(recent.max()) if not recent.empty else np.nan
    last = float(recent.iloc[-1]) if not recent.empty else np.nan
    drawdown = ((last / peak) - 1.0) * 100.0 if np.isfinite(peak) and peak > 0 and np.isfinite(last) else None

    positive = returns[returns > 0]
    negative = returns[returns < 0].abs()
    mean_gain = float(positive.mean()) if not positive.empty else None
    mean_loss = float(negative.mean()) if not negative.empty else None
    gain_loss_ratio = mean_loss / mean_gain if mean_gain and mean_gain > 0 and mean_loss is not None else None
    downside_vol = float(returns.where(returns < 0, 0.0).std(ddof=0) * 100.0) if not returns.empty else None

    risk_cfg = cfg.get("research_risk_diagnostics", {})
    drawdown_scale = max(float(risk_cfg.get("drawdown_risk_full_scale_pct", 12.0)), 1e-9)
    ratio_neutral = float(risk_cfg.get("gain_loss_ratio_neutral", 1.0))
    ratio_full = max(float(risk_cfg.get("gain_loss_ratio_full_scale", 2.0)), ratio_neutral + 1e-9)
    drawdown_risk = None if drawdown is None else clip_score(abs(min(drawdown, 0.0)) / drawdown_scale * 100.0)
    ratio_risk = None
    if gain_loss_ratio is not None:
        ratio_risk = clip_score((gain_loss_ratio - ratio_neutral) / (ratio_full - ratio_neutral) * 100.0)
    asymmetry = mean_available([drawdown_risk, ratio_risk])
    return {
        "drawdown_20d_pct": drawdown,
        "gain_loss_ratio_20d": gain_loss_ratio,
        "downside_volatility_20d_pct": downside_vol,
        "asymmetric_risk_score": asymmetry,
    }


def _liquidity_diagnostics(context: dict, cfg: dict) -> tuple[float | None, str | None]:
    turnover = None
    for field in ("median_turnover_20d_eur_ct", "median_turnover_eur", "turnover_20d_median_eur"):
        turnover = finite(context.get(field))
        if turnover is not None:
            break
    if turnover is None:
        return None, None
    thresholds = cfg.get("data_quality_thresholds", {})
    floor = float(cfg["shadow_thresholds"].get("minimum_median_turnover_eur_research", 500000.0))
    preferred = max(float(thresholds.get("preferred_median_turnover_eur", 1000000.0)), floor)
    robust = max(float(thresholds.get("robust_median_turnover_eur", 3000000.0)), preferred)
    if turnover < floor:
        return 0.0, "LIQUIDITY_BELOW_FLOOR"
    if turnover < preferred:
        return 40.0, "LIQUIDITY_THIN"
    if turnover < robust:
        return 70.0, None
    return 100.0, None


def compute_action_ct_snapshot_v22_1(frame: pd.DataFrame, cfg: dict, context: dict | None = None) -> dict:
    """V22.1 CT challenger: V22.0 technical core plus governed context overlays."""
    context = context or {}
    base = compute_v22_0(frame, cfg, context)
    if base.get("status") != "SUCCESS_SHADOW":
        base["version_engine"] = ENGINE_VERSION
        return base

    th = cfg["shadow_thresholds"]
    entry_components = dict(base.get("entry_components") or {})
    exit_components = dict(base.get("exit_components") or {})

    quality_target = mean_available(
        [
            _context_score(context, "morningstar_action_score"),
            _context_score(context, "target_upside_growth_score"),
            _context_score(context, "target_upside_gt4_score"),
        ]
    )

    macro_evidence = truthy(context.get("macro_evidence_sufficient"))
    macro_score = _context_score(context, "sector_macro_score")
    if macro_evidence is False:
        macro_score = None
    theme_macro = mean_available(
        [
            _context_score(context, "theme_rotation_exposure_score"),
            _context_score(context, "theme_risk_adjusted_score"),
            _context_score(context, "theme_confluence_score"),
            macro_score,
        ]
    )

    entry_components["quality_target"] = quality_target
    entry_components["theme_macro"] = theme_macro
    entry_score, entry_coverage = weighted_score(entry_components, cfg["entry_weights"])

    valuation_discount = _context_score(context, "valuation_discount_score")
    theme_avcr = _context_score(context, "theme_weighted_AVCR")
    days_to_earnings = finite(context.get("days_to_earnings"))
    event_risk = 100.0 if days_to_earnings is not None and 0 <= days_to_earnings <= float(th["earnings_event_risk_days"]) else None
    valuation_risk = mean_available([None if valuation_discount is None else 100.0 - valuation_discount, theme_avcr])
    valuation_event_risk = mean_available([valuation_risk, event_risk])
    exit_components["valuation_event_risk"] = valuation_event_risk
    exit_score, exit_coverage = weighted_score(exit_components, cfg["exit_risk_weights"])

    trend_score = finite(base.get("trend_score"))
    momentum_score = finite(base.get("momentum_score"))
    weekly_score = finite(base.get("weekly_score"))
    sector_score = finite(base.get("sector_context_score"))
    catalyst_score = finite(base.get("catalyst_score"))
    ret20 = finite(base.get("return_20d"))
    sma50 = finite(base.get("sma50_ct"))
    price = finite(base.get("reference_close"))
    rvol = finite(base.get("daily_rvol_ct"))
    volume_accel = finite(base.get("volume_acceleration_ct"))

    confirmations = {
        "TREND": bool(trend_score is not None and trend_score >= 65.0 and sma50 is not None and price is not None and price >= sma50),
        "MOMENTUM": bool(momentum_score is not None and momentum_score >= 60.0 and ret20 is not None and ret20 > 0),
        "WEEKLY": bool(weekly_score is not None and weekly_score >= float(th["weekly_alignment_min"])),
        "VOLUME": bool((rvol is not None and rvol >= float(th["daily_rvol_confirmation"])) or (volume_accel is not None and volume_accel >= float(th["volume_acceleration_confirmation"]))),
        "SECTOR": bool(sector_score is not None and sector_score >= float(th["sector_rotation_support_min"])),
        "CATALYST": bool(catalyst_score is not None and catalyst_score >= 60.0),
        "QUALITY": bool(quality_target is not None and quality_target >= float(th["quality_target_support_min"])),
        "THEME_MACRO": bool(theme_macro is not None and theme_macro >= min(float(th["theme_support_min"]), float(th["macro_support_min"]))),
    }
    confirmation_count = int(sum(confirmations.values()))

    theme_overvaluation = bool(theme_avcr is not None and theme_avcr >= float(th["theme_avcr_risk_min"]))
    macro_adverse = bool(macro_score is not None and macro_score < 35.0)
    quality_weak = bool(quality_target is not None and quality_target < 35.0)
    entry_exit_conflict = bool(exit_score is not None and exit_score >= float(th["max_exit_risk_for_entry"]))

    inherited_state = str(base.get("entry_state") or "")
    hard_wait_states = {
        "LIQUIDITY_WARNING_SHADOW",
        "WEEKLY_CONFLICT_SHADOW",
        "WAIT_RISK_SHADOW",
        "WAIT_PULLBACK_SHADOW",
    }
    if entry_score is None or entry_coverage < float(th["minimum_entry_coverage"]):
        entry_state = "DATA_INSUFFICIENT"
    elif inherited_state in hard_wait_states:
        entry_state = inherited_state
    elif inherited_state == "ENTRY_CONFLICT_SHADOW" or entry_exit_conflict:
        entry_state = "ENTRY_CONFLICT_SHADOW"
    elif theme_overvaluation and macro_adverse:
        entry_state = "WAIT_CONTEXT_RISK_SHADOW"
    elif entry_score >= float(th["entry_strong"]) and confirmation_count >= int(th["entry_strong_min_confirmations"]) and confirmations["TREND"] and confirmations["WEEKLY"]:
        entry_state = "ENTRY_STRONG_SHADOW"
    elif entry_score >= float(th["entry_ready"]) and confirmation_count >= int(th["entry_ready_min_confirmations"]) and confirmations["TREND"]:
        entry_state = "ENTRY_READY_SHADOW"
    else:
        entry_state = "WAIT_SHADOW"

    exit_state_raw = str(base.get("exit_state_raw") or "")
    structural_exit = exit_state_raw in {"EXIT_WATCH_SHADOW", "EXIT_RISK_HIGH_CANDIDATE_SHADOW"}
    if exit_score is None or exit_coverage < 0.70:
        exit_state = "DATA_INSUFFICIENT"
    elif exit_score >= float(th["exit_risk_high"]) and (structural_exit or event_risk == 100.0):
        exit_state = "EXIT_RISK_HIGH_CANDIDATE_SHADOW"
    elif exit_score >= float(th["exit_watch"]) or structural_exit:
        exit_state = "EXIT_WATCH_SHADOW"
    else:
        exit_state = "HOLD_SUPPORTIVE_SHADOW"

    risk_diag = _asymmetric_risk(frame, cfg)
    liquidity_quality, liquidity_warning = _liquidity_diagnostics(context, cfg)
    context_richness = float(entry_coverage) * (1.0 if quality_target is not None or theme_macro is not None else 0.85)

    warnings = [warning for warning in str(base.get("warnings") or "").split("|") if warning]
    if theme_overvaluation:
        warnings.append("THEME_OVERVALUATION_RISK")
    if macro_adverse:
        warnings.append("MACRO_CONTEXT_ADVERSE")
    if quality_weak:
        warnings.append("QUALITY_TARGET_WEAK")
    if liquidity_warning:
        warnings.append(liquidity_warning)
    asymmetry_warning = float(cfg.get("research_risk_diagnostics", {}).get("warning_min", 70.0))
    if risk_diag["asymmetric_risk_score"] is not None and float(risk_diag["asymmetric_risk_score"]) >= asymmetry_warning:
        warnings.append("ASYMMETRIC_DOWNSIDE_RISK")
    warnings = list(dict.fromkeys(warnings))

    base.update(
        {
            "version_engine": ENGINE_VERSION,
            "entry_score": entry_score,
            "entry_coverage": entry_coverage,
            "entry_state": entry_state,
            "entry_confirmation_count": confirmation_count,
            "entry_confirmations": "|".join(key for key, value in confirmations.items() if value),
            "exit_risk_score": exit_score,
            "exit_coverage": exit_coverage,
            "exit_state_raw": exit_state,
            "quality_target_score": quality_target,
            "theme_macro_score": theme_macro,
            "valuation_event_risk_score": valuation_event_risk,
            "valuation_risk_score": valuation_risk,
            "event_risk_score": event_risk,
            "context_richness_score": context_richness,
            "liquidity_quality_score": liquidity_quality,
            **risk_diag,
            "theme_overvaluation_risk_ct": theme_overvaluation,
            "macro_context_adverse_ct": macro_adverse,
            "quality_target_weak_ct": quality_weak,
            "warnings": "|".join(warnings),
            "entry_components": entry_components,
            "exit_components": exit_components,
            "t1_t2_used": False,
            "intraday_data_used": False,
            "fixed_take_profit_enabled": False,
            "fixed_stop_loss_enabled": False,
            "real_orders_enabled": False,
        }
    )
    return base
