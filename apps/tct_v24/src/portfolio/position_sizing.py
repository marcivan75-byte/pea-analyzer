from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def _finite_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if np.isfinite(result) else default


def compute_final_position_size(
    setup: Dict[str, Any],
    meta_proba: float,
    p_adverse: float,
    expected_adverse_gap: float,
    days_to_earnings: float,
    base_risk_pct: float = 0.008,
    max_position_pct: float = 0.06,
    min_position_pct: float = 0.004,
    capital: float = 100_000,
    rl_mult: float = 1.0,
    config: Optional[dict] = None,
) -> Dict[str, Any]:
    """Combine meta-labeling, gap risk, liquidity and optional RL sizing."""
    cfg = config or {}
    pos_cfg = cfg.get("position_sizing", {}) if isinstance(cfg, dict) else {}
    meta_cfg = cfg.get("meta_labeling", {}) if isinstance(cfg, dict) else {}

    base_risk_pct = _finite_float(pos_cfg.get("base_risk_pct", base_risk_pct), base_risk_pct)
    max_position_pct = _finite_float(pos_cfg.get("max_position_pct", max_position_pct), max_position_pct)
    min_position_pct = _finite_float(pos_cfg.get("min_position_pct", min_position_pct), min_position_pct)
    capital = _finite_float(capital, 0.0)

    min_proba = _finite_float(meta_cfg.get("min_proba", 0.55), 0.55)
    reduced_proba = _finite_float(meta_cfg.get("reduced_proba", 0.65), 0.65)
    full_proba = _finite_float(meta_cfg.get("full_proba", 0.75), 0.75)

    meta_proba = _finite_float(meta_proba, 0.0)
    p_adverse = _finite_float(p_adverse, 1.0)
    expected_adverse_gap = _finite_float(expected_adverse_gap, -1.0)
    days = _finite_float(days_to_earnings, 99.0)

    reasons = []

    # Si le contrôle d'univers a déjà été appliqué, sa décision est contraignante.
    universe_status = str(setup.get("universe_status") or "").strip().upper()
    universe_block = universe_status in {"REJECT", "QUARANTINE"}
    if universe_block:
        reasons.append(f"UNIVERSE_{universe_status}")

    # --- Meta multiplier ---
    if meta_proba < min_proba:
        meta_mult = 0.0
        reasons.append("META_BELOW_MIN")
    elif meta_proba < reduced_proba:
        meta_mult = 0.55
    elif meta_proba < full_proba:
        meta_mult = 0.85
    else:
        meta_mult = 1.15

    # --- Gap multiplier ---
    if p_adverse >= 0.38 or expected_adverse_gap <= -0.09:
        gap_mult = 0.0
        reasons.append("GAP_RISK_HARD")
    elif p_adverse >= 0.28 or expected_adverse_gap <= -0.065:
        gap_mult = 0.35
    elif p_adverse >= 0.20 or expected_adverse_gap <= -0.045:
        gap_mult = 0.60
    else:
        gap_mult = 1.0

    if days <= 1:
        gap_mult = 0.0
        reasons.append("EARNINGS_J1")
    elif days <= 2:
        gap_mult *= 0.45
    elif days <= 3:
        gap_mult *= 0.70

    # --- Liquidity ---
    adv = _finite_float(setup.get("avg_dollar_volume_20d"), 0.0)
    if adv < 300_000:
        liq_mult = 0.0
        reasons.append("ILLIQUID_OR_MISSING_ADV")
    elif adv < 800_000:
        liq_mult = 0.50
    elif adv < 2_000_000:
        liq_mult = 0.80
    else:
        liq_mult = 1.0

    rl_mult = float(np.clip(_finite_float(rl_mult, 1.0), 0.0, 1.5))
    raw_mult = meta_mult * gap_mult * liq_mult * rl_mult
    if universe_block:
        raw_mult = 0.0

    max_position_pct = max(0.0, max_position_pct)
    min_position_pct = max(0.0, min(min_position_pct, max_position_pct or min_position_pct))
    position_pct = min(max(0.0, base_risk_pct) * raw_mult, max_position_pct)

    close = _finite_float(setup.get("close"), 0.0)
    shares = 0
    if capital <= 0:
        reasons.append("INVALID_CAPITAL")
    if close <= 0:
        reasons.append("INVALID_PRICE")

    if position_pct < min_position_pct or raw_mult <= 0 or capital <= 0 or close <= 0:
        decision = "IGNORE"
        position_pct = 0.0
    else:
        shares = int((capital * position_pct) / close)
        if shares < 1:
            decision = "IGNORE"
            position_pct = 0.0
            shares = 0
            reasons.append("POSITION_LT_ONE_SHARE")
        else:
            decision = "TAKE"

    time_stop = "J-1_CLOSE" if days <= 5 else "NORMAL_5D"

    return {
        "decision": decision,
        "position_pct": round(position_pct, 4),
        "shares": shares,
        "meta_mult": round(meta_mult, 3),
        "gap_mult": round(gap_mult, 3),
        "liq_mult": round(liq_mult, 3),
        "raw_mult": round(raw_mult, 3),
        "time_stop": time_stop,
        "rl_mult": round(rl_mult, 3),
        "sizing_reason": "|".join(dict.fromkeys(reasons)) if reasons else "OK",
    }
