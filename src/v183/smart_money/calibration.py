from __future__ import annotations


def _non_increasing(values: list[float]) -> bool:
    return all(a >= b for a, b in zip(values, values[1:]))


def _increasing(values: list[float]) -> bool:
    return all(a < b for a, b in zip(values, values[1:]))


def validate_calibration(cfg: dict) -> dict:
    """Validate RC calibration as a risk-budget contract.

    RC1 is deliberately structural: it constrains score contribution, monotonic
    recency and evidence behavior. It is *not* labelled as an empirical alpha
    fit until enough point-in-time Smart Money history exists for walk-forward
    validation.
    """
    caps = cfg["caps"]
    calibration = cfg.get("calibration", {})
    insider_recency = cfg["insiders"]["recency_bands"]
    threshold_recency = cfg["thresholds"]["recency_bands"]
    threshold_bands = cfg["thresholds"]["bands"]

    action_component_budget = sum(
        float(caps[name]) for name in ("insider", "significant_holder", "short", "tape")
    )
    etf_component_budget = sum(
        float(caps[name]) for name in ("flow_core", "flow_persistence", "etf_tape")
    )
    wis_cap = float(caps["wis"])
    ifs_cap = float(caps["ifs"])

    checks = {
        "status_rc1": calibration.get("status") == "STRUCTURAL_CALIBRATED_RC1",
        "active_scoring_disabled": calibration.get("active_scoring_allowed") is False,
        "walk_forward_required": calibration.get("empirical_walk_forward_required_for_active_scoring") is True,
        "shadow_only": cfg.get("shadow_mode") is True and cfg.get("score_application") == "SHADOW_ONLY",
        "action_cap_matches_target": wis_cap <= float(calibration.get("target_max_action_score_delta", 0)),
        "etf_cap_matches_target": ifs_cap <= float(calibration.get("target_max_etf_score_delta", 0)),
        "wis_cap_not_above_component_budget": wis_cap <= action_component_budget,
        "ifs_cap_not_above_component_budget": ifs_cap <= etf_component_budget,
        "insider_recency_monotone": _increasing([float(x["max_days"]) for x in insider_recency])
        and _non_increasing([float(x["factor"]) for x in insider_recency]),
        "threshold_recency_monotone": _increasing([float(x["max_days"]) for x in threshold_recency])
        and _non_increasing([float(x["factor"]) for x in threshold_recency]),
        "threshold_weights_monotone": _increasing([float(x["threshold_pct"]) for x in threshold_bands])
        and all(a <= b for a, b in zip(
            [float(x["weight"]) for x in threshold_bands],
            [float(x["weight"]) for x in threshold_bands][1:],
        )),
        "sell_asymmetry_conservative": 0.0 < float(cfg["insiders"]["sell_asymmetry"]) < 1.0,
        "confidence_low_cap_bounded": float(cfg["confidence"]["low_confidence_effective_cap"]) < wis_cap,
        "etf_flow_requires_nav": cfg["etf_flows"].get("require_nav_adjustment") is True,
        "raw_aum_flow_forbidden": cfg["etf_flows"].get("aum_change_alone_forbidden") is True,
    }
    passed = all(checks.values())
    return {
        "status": calibration.get("status"),
        "method": calibration.get("method"),
        "passed": passed,
        "empirical_walk_forward_required_for_active_scoring": bool(
            calibration.get("empirical_walk_forward_required_for_active_scoring", True)
        ),
        "action_component_budget": round(action_component_budget, 4),
        "action_effective_cap": wis_cap,
        "etf_component_budget": round(etf_component_budget, 4),
        "etf_effective_cap": ifs_cap,
        "checks": [{"check": k, "passed": bool(v)} for k, v in checks.items()],
    }
