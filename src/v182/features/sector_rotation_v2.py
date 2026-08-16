from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import json

import numpy as np
import pandas as pd


NEUTRAL = 50.0


def _clip(value: float | int | None, lo: float = 0.0, hi: float = 100.0) -> float:
    if value is None or not np.isfinite(float(value)):
        return NEUTRAL
    return float(np.clip(float(value), lo, hi))


def _num(frame: pd.DataFrame, field: str) -> pd.Series:
    if field not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[field], errors="coerce")


def _first_numeric(frame: pd.DataFrame, aliases: Iterable[str]) -> tuple[pd.Series, str | None]:
    for field in aliases:
        if field in frame.columns:
            series = _num(frame, field)
            if series.notna().any():
                return series, field
    return pd.Series(np.nan, index=frame.index, dtype=float), None


def _bool_pct(series: pd.Series) -> float | None:
    if series.empty:
        return None
    text = series.astype(str).str.strip().str.lower()
    values = pd.Series(np.nan, index=series.index, dtype=float)
    values.loc[text.isin({"true", "1", "yes", "oui"})] = 1.0
    values.loc[text.isin({"false", "0", "no", "non"})] = 0.0
    numeric = pd.to_numeric(series, errors="coerce")
    values = values.where(values.notna(), numeric.where(numeric.isin([0, 1])))
    return float(values.mean() * 100.0) if values.notna().any() else None


def _sector_series(frame: pd.DataFrame) -> pd.Series:
    output = pd.Series("NON_CLASSE", index=frame.index, dtype=object)
    for field in ("sector_yf", "sector_yahoo", "sector", "sector_bucket", "industry_yf"):
        if field not in frame.columns:
            continue
        raw = frame[field].astype(str).str.strip()
        valid = ~raw.str.lower().isin({"", "nan", "none", "n/a", "na", "unknown"})
        output = output.where(~((output == "NON_CLASSE") & valid), raw)
    return output


def _rank(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() <= 1:
        return pd.Series(NEUTRAL, index=series.index, dtype=float)
    return (numeric.rank(method="average", pct=True, ascending=True) * 100.0).fillna(NEUTRAL)


def _median(series: pd.Series) -> float | None:
    numeric = pd.to_numeric(series, errors="coerce")
    return float(numeric.median()) if numeric.notna().any() else None


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _weighted_available(values: dict[str, float | None], weights: dict[str, float]) -> tuple[float, float]:
    available = {
        key: float(value)
        for key, value in values.items()
        if key in weights and value is not None and np.isfinite(float(value))
    }
    if not available:
        return NEUTRAL, 0.0
    used_weight = sum(float(weights[key]) for key in available)
    total_weight = sum(float(value) for value in weights.values()) or 1.0
    if used_weight <= 0:
        return NEUTRAL, 0.0
    raw = sum(available[key] * float(weights[key]) for key in available) / used_weight
    coverage = min(1.0, used_weight / total_weight)
    # Missing factor families shrink the result toward neutral instead of disappearing.
    score = NEUTRAL + coverage * (raw - NEUTRAL)
    return _clip(score), float(coverage)


def load_config(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class SectorRotationResult:
    sectors: pd.DataFrame
    diagnostic: dict[str, Any]


def _build_sector_base(actions: pd.DataFrame, aliases: dict[str, list[str]]) -> tuple[pd.DataFrame, dict[str, str | None]]:
    work = actions.copy()
    work["_sector"] = _sector_series(work)
    resolved: dict[str, str | None] = {}
    values: dict[str, pd.Series] = {}
    for logical, names in aliases.items():
        values[logical], resolved[logical] = _first_numeric(work, names)

    above50 = work[resolved["above_mm50"]] if resolved.get("above_mm50") else pd.Series(pd.NA, index=work.index)
    above200 = work[resolved["above_mm200"]] if resolved.get("above_mm200") else pd.Series(pd.NA, index=work.index)

    rows: list[dict[str, Any]] = []
    for sector, idx in work.groupby("_sector").groups.items():
        if sector == "NON_CLASSE" or len(idx) < 3:
            continue
        row: dict[str, Any] = {"sector": str(sector), "n_actions": int(len(idx))}
        for logical, series in values.items():
            if logical in {"above_mm50", "above_mm200"}:
                continue
            row[f"median_{logical}"] = _median(series.loc[idx])
        row["breadth_mm50"] = _bool_pct(above50.loc[idx])
        row["breadth_mm200"] = _bool_pct(above200.loc[idx])
        p1 = _optional_float(row.get("median_perf_1m"))
        p3 = _optional_float(row.get("median_perf_3m"))
        row["momentum_acceleration"] = p1 - p3 / 3.0 if p1 is not None and p3 is not None else None
        p1_values = values.get("perf_1m", pd.Series(np.nan, index=work.index)).loc[idx]
        p1_values = pd.to_numeric(p1_values, errors="coerce")
        row["positive_1m_share"] = float((p1_values > 0).mean() * 100.0) if p1_values.notna().any() else None
        rows.append(row)

    base = pd.DataFrame(rows)
    if base.empty:
        return base, resolved

    ranked_metrics = (
        "median_perf_1m",
        "median_perf_3m",
        "median_perf_6m",
        "momentum_acceleration",
        "breadth_mm50",
        "breadth_mm200",
        "positive_1m_share",
        "median_volume_ratio",
        "median_revenue_growth",
        "median_earnings_growth",
        "median_eps_revision",
    )
    for metric in ranked_metrics:
        if metric in base.columns:
            base[f"rank_{metric}"] = _rank(base[metric])

    for metric in ("median_pe", "median_pb", "median_ps", "median_volatility", "median_beta"):
        if metric in base.columns:
            base[f"risk_rank_{metric}"] = _rank(base[metric])

    market_p1 = _median(values.get("perf_1m", pd.Series(np.nan, index=work.index))) or 0.0
    base["relative_strength_1m"] = pd.to_numeric(base["median_perf_1m"], errors="coerce") - market_p1
    base["rank_relative_strength_1m"] = _rank(base["relative_strength_1m"])
    return base, resolved


def _mean_available(values: Iterable[Any], default: float = NEUTRAL) -> float:
    clean = [_optional_float(value) for value in values]
    clean = [value for value in clean if value is not None]
    return _clip(float(np.mean(clean)) if clean else default)


def _score_row(row: pd.Series, cfg: dict[str, Any]) -> dict[str, Any]:
    breadth = _mean_available((row.get("breadth_mm50"), row.get("breadth_mm200")))
    growth = _mean_available((row.get("rank_median_revenue_growth"), row.get("rank_median_earnings_growth")))
    eps_revision = _optional_float(row.get("rank_median_eps_revision"))
    relative_strength = _optional_float(row.get("rank_relative_strength_1m"))
    acceleration = _optional_float(row.get("rank_momentum_acceleration"))
    diffusion = _optional_float(row.get("positive_1m_share"))
    early_price_volume = _mean_available((row.get("rank_median_perf_1m"), row.get("rank_median_volume_ratio")))

    flow_score = _optional_float(row.get("median_sector_flow_score"))
    macro_score = _optional_float(row.get("median_sector_macro_score"))
    catalyst_score = _optional_float(row.get("median_sector_catalyst_score"))
    structural_score = _optional_float(row.get("median_sector_structural_score"))
    theme_confluence = _optional_float(row.get("median_theme_confluence_score"))
    valuation_history = _optional_float(row.get("median_sector_valuation_history_percentile"))

    rls_components = {
        "earnings_revisions": eps_revision,
        "breadth": breadth,
        "relative_strength": relative_strength,
        "flows": flow_score,
        "fundamental_acceleration": growth,
        "macro_compatibility": macro_score,
        "catalysts": catalyst_score,
        "early_price_volume": early_price_volume,
        "internal_diffusion": diffusion,
    }
    rls, rls_coverage = _weighted_available(rls_components, cfg["score_weights"]["RLS"])

    sqs = _mean_available((breadth, growth, eps_revision))
    cts = _mean_available((row.get("rank_median_perf_1m"), acceleration, relative_strength, breadth))
    sts = _clip(structural_score if structural_score is not None else NEUTRAL)
    mcs = _mean_available((row.get("rank_median_perf_1m"), relative_strength, breadth, diffusion))

    valuation_market = _mean_available(
        (row.get("risk_rank_median_pe"), row.get("risk_rank_median_pb"), row.get("risk_rank_median_ps"))
    )
    has_valuation = any(
        _optional_float(row.get(name)) is not None
        for name in ("risk_rank_median_pe", "risk_rank_median_pb", "risk_rank_median_ps")
    )
    valuation_market_value = valuation_market if has_valuation else None

    perf3 = _optional_float(row.get("rank_median_perf_3m"))
    price_fund_gap = _clip(50.0 + (perf3 - growth) * 0.75) if perf3 is not None else None

    perf1_rank = _optional_float(row.get("rank_median_perf_1m"))
    distance_high = _optional_float(row.get("median_distance_high_52w"))
    near_high = None if distance_high is None else _clip(100.0 - min(100.0, max(0.0, distance_high) * 5.0))
    technical_overextension = None
    if perf1_rank is not None or near_high is not None:
        technical_overextension = _mean_available((perf1_rank, near_high))

    volume_rank = _optional_float(row.get("rank_median_volume_ratio"))
    crowding = None
    if volume_rank is not None or perf1_rank is not None:
        crowding = _mean_available((volume_rank, perf1_rank))

    breadth_divergence = None
    if perf1_rank is not None:
        breadth_divergence = _clip(50.0 + (perf1_rank - breadth) * 0.8)

    expectation_fragility = None
    if valuation_market_value is not None:
        expectation_fragility = _mean_available((valuation_market_value, technical_overextension, crowding))

    volatility_rank = _optional_float(row.get("risk_rank_median_volatility"))
    avcr_components = {
        "valuation_vs_history": valuation_history,
        "valuation_vs_market": valuation_market_value,
        "price_fundamental_gap": price_fund_gap,
        "technical_overextension": technical_overextension,
        "crowding": crowding,
        "breadth_divergence": breadth_divergence,
        "multiple_expansion_dependency": price_fund_gap,
        "expectation_fragility": expectation_fragility,
        "volatility_regime": volatility_rank,
    }
    raw_vcr, vcr_coverage = _weighted_available(avcr_components, cfg["score_weights"]["AVCR"])
    valuation_justification = _mean_available((growth, eps_revision, breadth))
    avcr = _clip(raw_vcr - 0.35 * (valuation_justification - NEUTRAL))
    margin_of_safety = _clip(100.0 - avcr)

    essential = {
        "momentum": row.get("median_perf_1m"),
        "medium_momentum": row.get("median_perf_3m"),
        "breadth50": row.get("breadth_mm50"),
        "breadth200": row.get("breadth_mm200"),
        "earnings_revision": row.get("median_eps_revision"),
        "revenue_growth": row.get("median_revenue_growth"),
        "earnings_growth": row.get("median_earnings_growth"),
        "valuation": valuation_market_value,
        "volume": row.get("median_volume_ratio"),
        "sector_catalyst": catalyst_score,
    }
    completeness = 100.0 * sum(_optional_float(value) is not None for value in essential.values()) / len(essential)
    source_reliability = 75.0
    pit_quality = 80.0
    freshness = 85.0
    sector_coverage = _clip(min(100.0, float(row.get("n_actions", 0)) / 20.0 * 100.0))
    dqs_weights = cfg["score_weights"]["DQS"]
    dqs = _clip(
        dqs_weights["freshness"] * freshness
        + dqs_weights["completeness"] * completeness
        + dqs_weights["pit_quality"] * pit_quality
        + dqs_weights["source_reliability"] * source_reliability
        + dqs_weights["sector_coverage"] * sector_coverage
    )

    opportunity_values = {
        "RLS": rls,
        "SQS": sqs,
        "CTS": cts,
        "STS": sts,
        "MCS": mcs,
        "margin_of_safety": margin_of_safety,
        "theme_confluence": _clip(theme_confluence if theme_confluence is not None else NEUTRAL),
    }
    opportunity_weights = cfg["score_weights"]["RARS_OPPORTUNITY"]
    opportunity = sum(float(opportunity_values[key]) * float(opportunity_weights[key]) for key in opportunity_weights)
    risk_adjustment = 1.0 - 0.35 * avcr / 100.0
    data_adjustment = 0.70 + 0.30 * dqs / 100.0
    rars = _clip(opportunity * risk_adjustment * data_adjustment)

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
        "DQS": round(dqs, 4),
        "RARS": round(rars, 4),
        "breadth_score": round(breadth, 4),
        "technical_overextension": None if technical_overextension is None else round(technical_overextension, 4),
        "crowding": None if crowding is None else round(crowding, 4),
        "breadth_divergence": None if breadth_divergence is None else round(breadth_divergence, 4),
        "price_fundamental_gap": None if price_fund_gap is None else round(price_fund_gap, 4),
        "expectation_fragility": None if expectation_fragility is None else round(expectation_fragility, 4),
        "volatility_risk": None if volatility_rank is None else round(volatility_rank, 4),
        "sector_flow_score": flow_score,
        "sector_macro_score": macro_score,
        "sector_catalyst_score": catalyst_score,
        "theme_confluence_score": theme_confluence,
        "rls_components": rls_components,
        "avcr_components": avcr_components,
        "data_completeness_pct": round(completeness, 4),
    }


def _history_context(history: pd.DataFrame | None, sector: str, as_of: str) -> dict[str, Any]:
    context = {"prior": None, "prior_velocity": None, "days_in_state": 0}
    if history is None or history.empty or "sector" not in history.columns:
        return context
    subset = history.loc[history["sector"].astype(str) == str(sector)].copy()
    if subset.empty:
        return context
    subset["_as_of"] = pd.to_datetime(subset.get("as_of"), errors="coerce", utc=True)
    current_date = pd.to_datetime(as_of, errors="coerce", utc=True)
    subset = subset.loc[subset["_as_of"].notna() & (subset["_as_of"] < current_date)].sort_values("_as_of")
    if subset.empty:
        return context
    prior = subset.iloc[-1]
    context["prior"] = prior
    context["prior_velocity"] = _optional_float(prior.get("RLS_velocity"))
    prior_state = str(prior.get("state", ""))
    if prior_state:
        same_start = prior["_as_of"]
        for _, item in subset.iloc[::-1].iterrows():
            if str(item.get("state", "")) != prior_state:
                break
            same_start = item["_as_of"]
        context["days_in_state"] = max(0, int((current_date - same_start).days))
    return context


def _valuation_state(avcr: float, cfg: dict[str, Any]) -> str:
    thresholds = cfg["valuation_thresholds"]
    if avcr <= thresholds["normal_max"]:
        return "NORMAL"
    if avcr <= thresholds["watch_max"]:
        return "WATCH"
    if avcr <= thresholds["caution_max"]:
        return "VALUATION_CAUTION"
    if avcr <= thresholds["warning_max"]:
        return "OVERVALUATION_WARNING"
    if avcr <= thresholds["high_risk_max"]:
        return "HIGH_CORRECTION_RISK"
    return "EXTREME_CORRECTION_RISK"


def _reentry(score: dict[str, Any], breadth_delta: float, prior: pd.Series | None, correction_alert: bool, cfg: dict[str, Any]) -> tuple[float, str]:
    prior_warning = False
    if prior is not None:
        prior_warning = bool(prior.get("correction_alert", False)) or "CORRECTION_ALERT" in str(prior.get("warnings", ""))
    if not prior_warning and not correction_alert:
        return 0.0, "NOT_APPLICABLE"

    weights = cfg["reentry"]["weights"]
    components = {
        "valuation_normalization": 100.0 - float(score["AVCR"]),
        "technical_normalization": 100.0 - float(score["technical_overextension"] or NEUTRAL),
        "fundamentals_intact": float(score["SQS"]),
        "breadth_recovery": _clip(50.0 + breadth_delta * 2.0),
        "flow_stabilization": float(score["sector_flow_score"] or NEUTRAL),
        "price_structure": float(score["MCS"]),
        "volatility_normalization": 100.0 - float(score["volatility_risk"] or NEUTRAL),
    }
    readiness = _clip(sum(components[key] * float(weights[key]) for key in weights))
    if correction_alert:
        return readiness, "NOT_READY"
    if readiness >= cfg["reentry"]["ready_min"]:
        return readiness, "REENTRY_READY"
    if readiness >= cfg["reentry"]["forming_min"]:
        return readiness, "REENTRY_FORMING"
    if readiness >= cfg["reentry"]["watch_min"]:
        return readiness, "WATCH_REENTRY"
    return readiness, "NOT_READY"


def _state_and_warnings(
    row: dict[str, Any],
    context: dict[str, Any],
    cfg: dict[str, Any],
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
    if sqs < 45.0:
        families["fundamentals"] = True

    correction_alert = avcr >= 65.0 and sum(bool(value) for value in families.values()) >= int(
        warning_rules["correction_alert_min_independent_families"]
    )
    if correction_alert:
        warnings.append("CORRECTION_ALERT")
    if state in {"LEADERSHIP", "MATURE_LEADERSHIP"} and avcr >= 65.0:
        warnings.append("BULLISH_BUT_OVEREXTENDED")

    warning_confidence = min(dqs, 50.0 + 10.0 * len(families)) if warnings else 0.0
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

    reentry_readiness, reentry_state = _reentry(row, breadth_delta, prior, correction_alert, cfg)
    return {
        "state": state,
        "valuation_state": _valuation_state(avcr, cfg),
        "warnings": sorted(set(warnings)),
        "warning_confidence": round(float(warning_confidence), 4),
        "independent_warning_families": int(sum(bool(value) for value in families.values())),
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
) -> SectorRotationResult:
    """Build an explainable decision-isolated Sector Rotation V2 shadow snapshot."""
    if actions.empty:
        return SectorRotationResult(pd.DataFrame(), {"status": "EMPTY", "version": config.get("version")})

    aliases = config.get("field_aliases", {})
    base, field_resolution = _build_sector_base(actions, aliases)
    if base.empty:
        return SectorRotationResult(pd.DataFrame(), {"status": "NO_SECTORS", "version": config.get("version")})

    snapshot_date = as_of or datetime.now(timezone.utc).date().isoformat()
    rows: list[dict[str, Any]] = []
    for _, base_row in base.iterrows():
        score = _score_row(base_row, config)
        context = _history_context(history, str(base_row["sector"]), snapshot_date)
        state = _state_and_warnings(score, context, config)
        rows.append(
            {
                **base_row.to_dict(),
                **{key: value for key, value in score.items() if key not in {"rls_components", "avcr_components"}},
                **state,
                "rls_components": json.dumps(score["rls_components"], ensure_ascii=False, sort_keys=True),
                "avcr_components": json.dumps(score["avcr_components"], ensure_ascii=False, sort_keys=True),
                "as_of": snapshot_date,
                "model_version": config.get("version", "SECTOR_ROTATION_V2"),
                "mode": config.get("mode", "SHADOW_ONLY"),
            }
        )

    sectors = pd.DataFrame(rows).sort_values(["RARS", "RLS"], ascending=[False, False]).reset_index(drop=True)
    sectors["rank"] = np.arange(1, len(sectors) + 1)
    diagnostic = {
        "status": "OK",
        "version": config.get("version"),
        "mode": config.get("mode"),
        "as_of": snapshot_date,
        "sector_count": int(len(sectors)),
        "field_resolution": field_resolution,
        "average_DQS": round(float(sectors["DQS"].mean()), 4),
        "average_RLS": round(float(sectors["RLS"].mean()), 4),
        "average_AVCR": round(float(sectors["AVCR"].mean()), 4),
        "promising_but_overvalued": sectors.loc[
            sectors["warnings"].apply(lambda value: "PROMISING_BUT_OVERVALUED" in value), "sector"
        ].tolist(),
        "correction_alerts": sectors.loc[sectors["correction_alert"], "sector"].tolist(),
        "priority_candidates": sectors.loc[sectors["new_position_action"].eq("PRIORITY_BUY_ZONE"), "sector"].tolist(),
        "reentry_ready": sectors.loc[sectors["reentry_state"].eq("REENTRY_READY"), "sector"].tolist(),
        "governance": config.get("governance", {}),
    }
    return SectorRotationResult(sectors=sectors, diagnostic=diagnostic)


def append_history(snapshot: pd.DataFrame, path: str | Path) -> None:
    """Append one row per sector/date/model version without duplicate snapshots."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        old = pd.read_csv(output, sep=";", encoding="utf-8-sig", low_memory=False)
        combined = pd.concat([old, snapshot], ignore_index=True, sort=False)
    else:
        combined = snapshot.copy()
    keys = [column for column in ("sector", "as_of", "model_version") if column in combined.columns]
    if keys:
        combined = combined.drop_duplicates(keys, keep="last")
    combined.to_csv(output, sep=";", index=False, encoding="utf-8-sig")
