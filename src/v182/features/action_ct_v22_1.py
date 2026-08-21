from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from v182.features.action_ct_v22_0 import compute_action_ct_snapshot as compute_v22_0


def _finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _clip(value: float) -> float:
    return float(np.clip(value, 0.0, 100.0))


def _mean(values: list[float | None]) -> float | None:
    clean = [_finite(v) for v in values]
    clean = [v for v in clean if v is not None]
    return float(np.mean(clean)) if clean else None


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


def _context_score(context: dict, key: str) -> float | None:
    value = _finite(context.get(key))
    if value is None or not 0.0 <= value <= 100.0:
        return None
    return value


def _truthy(value: Any) -> bool | None:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "oui"}:
        return True
    if text in {"false", "0", "no", "non"}:
        return False
    return None


def compute_action_ct_snapshot_v22_1(frame: pd.DataFrame, cfg: dict, context: dict | None = None) -> dict:
    """V22.1 CT challenger: V22.0 technical core plus governed context overlays."""
    context = context or {}
    base = compute_v22_0(frame, cfg, context)
    if base.get("status") != "SUCCESS_SHADOW":
        base["version_engine"] = "ACTION_CT_V22.1.0_CONTEXT_ENRICHED_SHADOW"
        return base

    th = cfg["shadow_thresholds"]
    entry_components = dict(base.get("entry_components") or {})
    exit_components = dict(base.get("exit_components") or {})

    quality_target = _mean(
        [
            _context_score(context, "morningstar_action_score"),
            _context_score(context, "target_upside_growth_score"),
            _context_score(context, "target_upside_gt4_score"),
        ]
    )

    macro_evidence = _truthy(context.get("macro_evidence_sufficient"))
    macro_score = _context_score(context, "sector_macro_score")
    if macro_evidence is False:
        macro_score = None
    theme_macro = _mean(
        [
            _context_score(context, "theme_rotation_exposure_score"),
            _context_score(context, "theme_risk_adjusted_score"),
            _context_score(context, "theme_confluence_score"),
            macro_score,
        ]
    )

    entry_components["quality_target"] = quality_target
    entry_components["theme_macro"] = theme_macro
    entry_score, entry_coverage = _weighted(entry_components, cfg["entry_weights"])

    valuation_discount = _context_score(context, "valuation_discount_score")
    theme_avcr = _context_score(context, "theme_weighted_AVCR")
    days_to_earnings = _finite(context.get("days_to_earnings"))
    event_risk = 100.0 if days_to_earnings is not None and 0 <= days_to_earnings <= float(th["earnings_event_risk_days"]) else None
    valuation_event_risk = _mean(
        [
            None if valuation_discount is None else 100.0 - valuation_discount,
            theme_avcr,
            event_risk,
        ]
    )
    exit_components["valuation_event_risk"] = valuation_event_risk
    exit_score, exit_coverage = _weighted(exit_components, cfg["exit_risk_weights"])

    trend_score = _finite(base.get("trend_score"))
    momentum_score = _finite(base.get("momentum_score"))
    weekly_score = _finite(base.get("weekly_score"))
    sector_score = _finite(base.get("sector_context_score"))
    volume_score = _finite(base.get("volume_score"))
    catalyst_score = _finite(base.get("catalyst_score"))
    ret20 = _finite(base.get("return_20d"))
    sma50 = _finite(base.get("sma50_ct"))
    price = _finite(base.get("reference_close"))
    rvol = _finite(base.get("daily_rvol_ct"))
    volume_accel = _finite(base.get("volume_acceleration_ct"))

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
        "DATA_INSUFFICIENT",
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

    warnings = [w for w in str(base.get("warnings") or "").split("|") if w]
    if theme_overvaluation:
        warnings.append("THEME_OVERVALUATION_RISK")
    if macro_adverse:
        warnings.append("MACRO_CONTEXT_ADVERSE")
    if quality_weak:
        warnings.append("QUALITY_TARGET_WEAK")
    warnings = list(dict.fromkeys(warnings))

    base.update(
        {
            "version_engine": "ACTION_CT_V22.1.0_CONTEXT_ENRICHED_SHADOW",
            "entry_score": entry_score,
            "entry_coverage": entry_coverage,
            "entry_state": entry_state,
            "entry_confirmation_count": confirmation_count,
            "entry_confirmations": "|".join(k for k, v in confirmations.items() if v),
            "exit_risk_score": exit_score,
            "exit_coverage": exit_coverage,
            "exit_state_raw": exit_state,
            "quality_target_score": quality_target,
            "theme_macro_score": theme_macro,
            "valuation_event_risk_score": valuation_event_risk,
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
