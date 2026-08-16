from __future__ import annotations

from typing import Any, Iterable
import json

import numpy as np
import pandas as pd

from v182.features import sector_rotation_v2 as core


NEUTRAL = 50.0
_DYNAMIC_CORRECTION_FAMILIES = {"breadth", "rotation", "market_confirmation", "fundamental_deterioration"}


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _mean_optional(values: Iterable[Any]) -> float | None:
    clean = [_optional_float(value) for value in values]
    clean = [value for value in clean if value is not None]
    if not clean:
        return None
    return float(np.clip(float(np.mean(clean)), 0.0, 100.0))


def _neutral_if_missing(value: float | None) -> float:
    return NEUTRAL if value is None else float(np.clip(value, 0.0, 100.0))


def _present(row: pd.Series, field: str) -> bool:
    return _optional_float(row.get(field)) is not None


def _harden_scores(row: pd.Series, cfg: dict[str, Any]) -> dict[str, Any]:
    """Recalculate evidence-sensitive scores so missing families never count as present evidence."""
    breadth_optional = _mean_optional((row.get("breadth_mm50"), row.get("breadth_mm200")))
    growth_inputs: list[float] = []
    if _present(row, "median_revenue_growth"):
        rank = _optional_float(row.get("rank_median_revenue_growth"))
        if rank is not None:
            growth_inputs.append(rank)
    if _present(row, "median_earnings_growth"):
        rank = _optional_float(row.get("rank_median_earnings_growth"))
        if rank is not None:
            growth_inputs.append(rank)
    growth_optional = _mean_optional(growth_inputs)

    eps_revision = (
        _optional_float(row.get("rank_median_eps_revision"))
        if _present(row, "median_eps_revision")
        else None
    )
    relative_strength = (
        _optional_float(row.get("rank_relative_strength_1m"))
        if _present(row, "median_perf_1m")
        else None
    )
    acceleration = (
        _optional_float(row.get("rank_momentum_acceleration"))
        if _present(row, "momentum_acceleration")
        else None
    )
    diffusion = _optional_float(row.get("positive_1m_share")) if _present(row, "median_perf_1m") else None

    early_inputs: list[float] = []
    if _present(row, "median_perf_1m"):
        value = _optional_float(row.get("rank_median_perf_1m"))
        if value is not None:
            early_inputs.append(value)
    if _present(row, "median_volume_ratio"):
        value = _optional_float(row.get("rank_median_volume_ratio"))
        if value is not None:
            early_inputs.append(value)
    early_price_volume = _mean_optional(early_inputs)

    flow_score = _optional_float(row.get("sector_flow_score"))
    macro_score = _optional_float(row.get("sector_macro_score"))
    catalyst_score = _optional_float(row.get("sector_catalyst_score"))
    structural_score = _optional_float(row.get("median_sector_structural_score"))
    if structural_score is None:
        structural_score = _optional_float(row.get("sector_structural_score"))
    theme_confluence = _optional_float(row.get("theme_confluence_score"))
    valuation_history = _optional_float(row.get("median_sector_valuation_history_percentile"))
    if valuation_history is None:
        valuation_history = _optional_float(row.get("sector_valuation_history_percentile"))

    rls_components = {
        "earnings_revisions": eps_revision,
        "breadth": breadth_optional,
        "relative_strength": relative_strength,
        "flows": flow_score,
        "fundamental_acceleration": growth_optional,
        "macro_compatibility": macro_score,
        "catalysts": catalyst_score,
        "early_price_volume": early_price_volume,
        "internal_diffusion": diffusion,
    }
    rls, rls_coverage = core._weighted_available(rls_components, cfg["score_weights"]["RLS"])

    breadth = _neutral_if_missing(breadth_optional)
    sqs = _neutral_if_missing(_mean_optional((breadth_optional, growth_optional, eps_revision)))

    cts_inputs: list[float] = []
    if _present(row, "median_perf_1m"):
        value = _optional_float(row.get("rank_median_perf_1m"))
        if value is not None:
            cts_inputs.append(value)
    if acceleration is not None:
        cts_inputs.append(acceleration)
    if relative_strength is not None:
        cts_inputs.append(relative_strength)
    if breadth_optional is not None:
        cts_inputs.append(breadth_optional)
    cts = _neutral_if_missing(_mean_optional(cts_inputs))

    sts = core._clip(structural_score if structural_score is not None else NEUTRAL)
    mcs = _neutral_if_missing(
        _mean_optional(
            (
                _optional_float(row.get("rank_median_perf_1m")) if _present(row, "median_perf_1m") else None,
                relative_strength,
                breadth_optional,
                diffusion,
            )
        )
    )

    valuation_ranks: list[float] = []
    for raw_field, rank_field in (
        ("median_pe", "risk_rank_median_pe"),
        ("median_pb", "risk_rank_median_pb"),
        ("median_ps", "risk_rank_median_ps"),
    ):
        if _present(row, raw_field):
            value = _optional_float(row.get(rank_field))
            if value is not None:
                valuation_ranks.append(value)
    valuation_market = _mean_optional(valuation_ranks)

    perf3_rank = _optional_float(row.get("rank_median_perf_3m")) if _present(row, "median_perf_3m") else None
    price_fund_gap = None
    if perf3_rank is not None and growth_optional is not None:
        price_fund_gap = core._clip(50.0 + (perf3_rank - growth_optional) * 0.75)

    perf1_rank = _optional_float(row.get("rank_median_perf_1m")) if _present(row, "median_perf_1m") else None
    distance_high = _optional_float(row.get("median_distance_high_52w"))
    near_high = None if distance_high is None else core._clip(100.0 - min(100.0, max(0.0, distance_high) * 5.0))
    technical_overextension = _mean_optional((perf1_rank, near_high))

    volume_rank = _optional_float(row.get("rank_median_volume_ratio")) if _present(row, "median_volume_ratio") else None
    crowding = _mean_optional((volume_rank, perf1_rank))
    breadth_divergence = None
    if perf1_rank is not None and breadth_optional is not None:
        breadth_divergence = core._clip(50.0 + (perf1_rank - breadth_optional) * 0.8)
    expectation_fragility = (
        _mean_optional((valuation_market, technical_overextension, crowding))
        if valuation_market is not None
        else None
    )
    volatility_rank = (
        _optional_float(row.get("risk_rank_median_volatility"))
        if _present(row, "median_volatility")
        else None
    )

    avcr_components = {
        "valuation_vs_history": valuation_history,
        "valuation_vs_market": valuation_market,
        "price_fundamental_gap": price_fund_gap,
        "technical_overextension": technical_overextension,
        "crowding": crowding,
        "breadth_divergence": breadth_divergence,
        "multiple_expansion_dependency": price_fund_gap,
        "expectation_fragility": expectation_fragility,
        "volatility_regime": volatility_rank,
    }
    raw_vcr, vcr_coverage = core._weighted_available(avcr_components, cfg["score_weights"]["AVCR"])
    valuation_justification = _neutral_if_missing(_mean_optional((growth_optional, eps_revision, breadth_optional)))
    avcr = core._clip(raw_vcr - 0.35 * (valuation_justification - NEUTRAL))
    margin_of_safety = core._clip(100.0 - avcr)

    dqs = float(row["DQS"])
    opportunity_values = {
        "RLS": rls,
        "SQS": sqs,
        "CTS": cts,
        "STS": sts,
        "MCS": mcs,
        "margin_of_safety": margin_of_safety,
        "theme_confluence": core._clip(theme_confluence if theme_confluence is not None else NEUTRAL),
    }
    opportunity_weights = cfg["score_weights"]["RARS_OPPORTUNITY"]
    opportunity = sum(float(opportunity_values[key]) * float(opportunity_weights[key]) for key in opportunity_weights)
    risk_adjustment = 1.0 - 0.35 * avcr / 100.0
    data_adjustment = 0.70 + 0.30 * dqs / 100.0
    rars = core._clip(opportunity * risk_adjustment * data_adjustment)

    return {
        "RLS": round(rls, 4),
        "RLS_coverage": round(rls_coverage * 100.0, 4),
        "SQS": round(sqs, 4),
        "CTS": round(cts, 4),
        "STS": round(sts, 4),
        "MCS": round(mcs, 4),
        "raw_VCR": round(raw_vcr, 4),
        "VCR_coverage": round(vcr_coverage * 100.0, 4),
        "valuation_justification": round(valuation_justification, 4),
        "AVCR": round(avcr, 4),
        "margin_of_safety": round(margin_of_safety, 4),
        "RARS": round(rars, 4),
        "breadth_score": round(breadth, 4),
        "technical_overextension": None if technical_overextension is None else round(technical_overextension, 4),
        "crowding": None if crowding is None else round(crowding, 4),
        "breadth_divergence": None if breadth_divergence is None else round(breadth_divergence, 4),
        "price_fundamental_gap": None if price_fund_gap is None else round(price_fund_gap, 4),
        "expectation_fragility": None if expectation_fragility is None else round(expectation_fragility, 4),
        "volatility_risk": None if volatility_rank is None else round(volatility_rank, 4),
        "rls_components": rls_components,
        "avcr_components": avcr_components,
    }


def _reentry_final(
    row: dict[str, Any], breadth_delta: float, prior: pd.Series | None, correction_alert: bool, cfg: dict[str, Any]
) -> tuple[float, str]:
    prior_warning = False
    if prior is not None:
        prior_warning = bool(prior.get("correction_alert", False)) or "CORRECTION_ALERT" in str(prior.get("warnings", ""))
    if not prior_warning and not correction_alert:
        return 0.0, "NOT_APPLICABLE"

    technical = _optional_float(row.get("technical_overextension"))
    flow = _optional_float(row.get("sector_flow_score"))
    volatility = _optional_float(row.get("volatility_risk"))
    weights = cfg["reentry"]["weights"]
    components = {
        "valuation_normalization": 100.0 - float(row["AVCR"]),
        "technical_normalization": 100.0 - (NEUTRAL if technical is None else technical),
        "fundamentals_intact": float(row["SQS"]),
        "breadth_recovery": core._clip(50.0 + breadth_delta * 2.0),
        "flow_stabilization": NEUTRAL if flow is None else flow,
        "price_structure": float(row["MCS"]),
        "volatility_normalization": 100.0 - (NEUTRAL if volatility is None else volatility),
    }
    readiness = core._clip(sum(components[key] * float(weights[key]) for key in weights))
    if correction_alert:
        return readiness, "NOT_READY"
    if readiness >= cfg["reentry"]["ready_min"]:
        return readiness, "REENTRY_READY"
    if readiness >= cfg["reentry"]["forming_min"]:
        return readiness, "REENTRY_FORMING"
    if readiness >= cfg["reentry"]["watch_min"]:
        return readiness, "WATCH_REENTRY"
    return readiness, "NOT_READY"


def _state_and_warnings_final(
    row: dict[str, Any], context: dict[str, Any], cfg: dict[str, Any]
) -> dict[str, Any]:
    rls = float(row["RLS"])
    sqs = float(row["SQS"])
    mcs = float(row["MCS"])
    avcr = float(row["AVCR"])
    dqs = float(row["DQS"])
    thresholds = cfg["state_thresholds"]
    valuation = cfg["valuation_thresholds"]
    warning_rules = cfg["warning_rules"]
    prior = context.get("prior")

    prior_rls = _optional_float(prior.get("RLS")) if prior is not None else None
    prior_breadth = _optional_float(prior.get("breadth_score")) if prior is not None else None
    prior_sqs = _optional_float(prior.get("SQS")) if prior is not None else None
    velocity = rls - prior_rls if prior_rls is not None else 0.0
    prior_velocity = context.get("prior_velocity")
    acceleration = velocity - float(prior_velocity) if prior_velocity is not None else 0.0
    breadth_delta = float(row["breadth_score"]) - prior_breadth if prior_breadth is not None else 0.0

    if rls < thresholds["rotation_out_rls"] and velocity <= 0:
        state = "ROTATION_OUT"
    elif rls < thresholds["distribution_rls"] and velocity < 0:
        state = "DISTRIBUTION"
    elif rls >= thresholds["leadership_rls"] and sqs >= thresholds["leadership_sqs"] and mcs >= thresholds["leadership_mcs"]:
        state = "MATURE_LEADERSHIP" if velocity < 0 or avcr >= valuation["promising_but_overvalued_avcr_min"] else "LEADERSHIP"
    elif rls >= thresholds["confirmed_rotation_rls"] and mcs >= thresholds["confirmed_rotation_mcs"]:
        state = "CONFIRMED_ROTATION"
    elif rls >= thresholds["early_rotation_enter"] and velocity >= 0:
        state = "EARLY_ROTATION"
    elif rls >= 55.0 and velocity >= 0:
        state = "ACCUMULATION"
    else:
        state = "NEUTRAL"

    warnings: list[str] = []
    families: dict[str, bool] = {}
    if rls >= valuation["promising_but_overvalued_rls_min"] and avcr >= valuation["promising_but_overvalued_avcr_min"]:
        warnings.append("PROMISING_BUT_OVERVALUED")
        families["valuation"] = True
    technical = _optional_float(row.get("technical_overextension"))
    if technical is not None and technical >= warning_rules["technical_overextension_rank_min"]:
        warnings.append("TECHNICAL_OVEREXTENSION")
        families["technical"] = True
    crowding = _optional_float(row.get("crowding"))
    if crowding is not None and crowding >= warning_rules["crowding_rank_min"]:
        warnings.append("CROWDING_EUPHORIA")
        families["crowding"] = True
    if breadth_delta <= -float(warning_rules["leadership_narrowing_breadth_drop_pp"]):
        warnings.append("LEADERSHIP_NARROWING")
        families["breadth"] = True
    price_gap = _optional_float(row.get("price_fundamental_gap"))
    if price_gap is not None and price_gap >= 75.0:
        warnings.append("MULTIPLE_EXPANSION_DEPENDENCY")
        families["valuation"] = True
    if avcr >= warning_rules["perfection_priced_in_avcr_min"] and float(row["valuation_justification"]) < 60.0:
        warnings.append("PERFECTION_PRICED_IN")
        families["valuation"] = True
    if avcr <= warning_rules["value_trap_valuation_risk_max"] and rls <= warning_rules["value_trap_rls_max"]:
        warnings.append("VALUE_TRAP")
        families["fundamentals"] = True
    if velocity < -8.0:
        families["rotation"] = True
    if mcs < 45.0:
        families["market_confirmation"] = True
    if prior_sqs is not None and sqs <= prior_sqs - 8.0:
        families["fundamental_deterioration"] = True
    elif sqs < 40.0:
        families["fundamentals"] = True

    family_count = sum(bool(value) for value in families.values())
    dynamic_confirmation = any(bool(families.get(name)) for name in _DYNAMIC_CORRECTION_FAMILIES)
    correction_alert = (
        avcr >= 65.0
        and family_count >= int(warning_rules["correction_alert_min_independent_families"])
        and dynamic_confirmation
    )
    if correction_alert:
        warnings.append("CORRECTION_ALERT")
    if state in {"LEADERSHIP", "MATURE_LEADERSHIP"} and avcr >= 65.0:
        warnings.append("BULLISH_BUT_OVEREXTENDED")

    warning_confidence = min(dqs, 50.0 + 10.0 * family_count) if warnings else 0.0
    decisions = cfg["decision_thresholds"]
    min_signal_dqs = cfg["governance"]["minimum_dqs_for_signal"]
    min_decision_dqs = cfg["governance"]["minimum_dqs_for_decision"]

    if dqs < min_signal_dqs:
        new_action = "NO_ACTION_INSUFFICIENT_DATA"
    elif correction_alert:
        new_action = "NO_NEW_ENTRY"
    elif avcr >= decisions["no_chase_avcr_min"] and rls >= decisions["buy_rls_min"]:
        new_action = "NO_CHASE"
    elif rls >= decisions["buy_rls_min"] and decisions["accumulate_avcr_max"] < avcr <= decisions["wait_pullback_avcr_max"]:
        new_action = "WAIT_FOR_PULLBACK"
    elif rls >= decisions["buy_rls_min"] and decisions["buy_avcr_max"] < avcr <= decisions["accumulate_avcr_max"]:
        new_action = "ACCUMULATE_ON_WEAKNESS"
    elif (
        dqs >= min_decision_dqs
        and rls >= decisions["priority_buy_rls_min"]
        and sqs >= decisions["priority_buy_sqs_min"]
        and mcs >= decisions["priority_buy_mcs_min"]
        and avcr <= decisions["priority_buy_avcr_max"]
    ):
        new_action = "PRIORITY_BUY_ZONE"
    elif dqs >= min_decision_dqs and rls >= decisions["buy_rls_min"] and avcr <= decisions["buy_avcr_max"]:
        new_action = "BUY_ZONE"
    elif state in {"DISTRIBUTION", "ROTATION_OUT"}:
        new_action = "AVOID"
    else:
        new_action = "WATCH"

    if correction_alert or state == "ROTATION_OUT":
        existing_action = "EXIT_REVIEW"
    elif warnings:
        existing_action = "HOLD_MONITOR"
    else:
        existing_action = "HOLD"

    reentry_readiness, reentry_state = _reentry_final(row, breadth_delta, prior, correction_alert, cfg)
    return {
        "state": state,
        "valuation_state": core._valuation_state(avcr, cfg),
        "warnings": sorted(set(warnings)),
        "warning_confidence": round(float(warning_confidence), 4),
        "independent_warning_families": int(family_count),
        "dynamic_correction_confirmation": bool(dynamic_confirmation),
        "correction_alert": bool(correction_alert),
        "RLS_velocity": round(float(velocity), 4),
        "RLS_acceleration": round(float(acceleration), 4),
        "breadth_delta": round(float(breadth_delta), 4),
        "days_in_prior_state": int(context.get("days_in_state", 0)),
        "new_position_action": new_action,
        "existing_position_action": existing_action,
        "reentry_readiness": round(float(reentry_readiness), 4),
        "reentry_state": reentry_state,
    }


def build_sector_rotation_v2(
    actions: pd.DataFrame,
    config: dict[str, Any],
    *,
    history: pd.DataFrame | None = None,
    as_of: str | None = None,
) -> core.SectorRotationResult:
    """Authoritative V2 Shadow builder with final governance guardrails applied."""
    base = core.build_sector_rotation_v2(actions, config, history=history, as_of=as_of)
    if base.sectors.empty:
        return base

    sectors = base.sectors.copy()
    snapshot_date = str(sectors["as_of"].iloc[0])
    hardened_rows: list[dict[str, Any]] = []
    for _, row in sectors.iterrows():
        hardened = _harden_scores(row, config)
        merged = {**row.to_dict(), **{key: value for key, value in hardened.items() if key not in {"rls_components", "avcr_components"}}}
        context = core._history_context(history, str(row["sector"]), snapshot_date)
        state = _state_and_warnings_final(merged, context, config)
        merged.update(state)
        merged["rls_components"] = json.dumps(hardened["rls_components"], ensure_ascii=False, sort_keys=True)
        merged["avcr_components"] = json.dumps(hardened["avcr_components"], ensure_ascii=False, sort_keys=True)
        merged["execution_layer"] = "FINAL_GOVERNANCE_GUARDRAILS"
        hardened_rows.append(merged)

    final = pd.DataFrame(hardened_rows).sort_values(["RARS", "RLS"], ascending=[False, False]).reset_index(drop=True)
    final["rank"] = np.arange(1, len(final) + 1)
    diagnostic = dict(base.diagnostic)
    diagnostic.update(
        {
            "execution_layer": "FINAL_GOVERNANCE_GUARDRAILS",
            "average_DQS": round(float(final["DQS"].mean()), 4),
            "average_RLS": round(float(final["RLS"].mean()), 4),
            "average_RLS_coverage": round(float(final["RLS_coverage"].mean()), 4),
            "average_AVCR": round(float(final["AVCR"].mean()), 4),
            "promising_but_overvalued": final.loc[
                final["warnings"].apply(lambda value: "PROMISING_BUT_OVERVALUED" in value), "sector"
            ].tolist(),
            "correction_alerts": final.loc[final["correction_alert"], "sector"].tolist(),
            "priority_candidates": final.loc[final["new_position_action"].eq("PRIORITY_BUY_ZONE"), "sector"].tolist(),
            "reentry_ready": final.loc[final["reentry_state"].eq("REENTRY_READY"), "sector"].tolist(),
            "correction_alert_requires_dynamic_confirmation": True,
        }
    )
    return core.SectorRotationResult(sectors=final, diagnostic=diagnostic)


append_history = core.append_history
load_config = core.load_config
