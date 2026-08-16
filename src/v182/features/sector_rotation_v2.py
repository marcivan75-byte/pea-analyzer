from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import json
import math

import numpy as np
import pandas as pd


NEUTRAL = 50.0


def _clip(v: float | int | None, lo: float = 0.0, hi: float = 100.0) -> float:
    if v is None or not np.isfinite(float(v)):
        return NEUTRAL
    return float(np.clip(float(v), lo, hi))


def _num(frame: pd.DataFrame, field: str) -> pd.Series:
    if field not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[field], errors="coerce")


def _first_numeric(frame: pd.DataFrame, aliases: Iterable[str]) -> tuple[pd.Series, str | None]:
    for field in aliases:
        if field in frame.columns:
            s = _num(frame, field)
            if s.notna().any():
                return s, field
    return pd.Series(np.nan, index=frame.index, dtype=float), None


def _bool_pct(series: pd.Series) -> float | None:
    if series.empty:
        return None
    text = series.astype(str).str.strip().str.lower()
    vals = pd.Series(np.nan, index=series.index, dtype=float)
    vals.loc[text.isin({"true", "1", "yes", "oui"})] = 1.0
    vals.loc[text.isin({"false", "0", "no", "non"})] = 0.0
    numeric = pd.to_numeric(series, errors="coerce")
    vals = vals.where(vals.notna(), numeric.where(numeric.isin([0, 1])))
    return float(vals.mean() * 100.0) if vals.notna().any() else None


def _sector_series(frame: pd.DataFrame) -> pd.Series:
    out = pd.Series("NON_CLASSE", index=frame.index, dtype=object)
    for field in ("sector_yf", "sector_yahoo", "sector", "sector_bucket", "industry_yf"):
        if field not in frame.columns:
            continue
        raw = frame[field].astype(str).str.strip()
        valid = ~raw.str.lower().isin({"", "nan", "none", "n/a", "na", "unknown"})
        out = out.where(~((out == "NON_CLASSE") & valid), raw)
    return out


def _rank(series: pd.Series, *, ascending: bool = True) -> pd.Series:
    x = pd.to_numeric(series, errors="coerce")
    if x.notna().sum() <= 1:
        return pd.Series(NEUTRAL, index=series.index, dtype=float)
    return (x.rank(method="average", pct=True, ascending=ascending) * 100.0).fillna(NEUTRAL)


def _median(series: pd.Series) -> float | None:
    s = pd.to_numeric(series, errors="coerce")
    return float(s.median()) if s.notna().any() else None


def _weighted_available(values: dict[str, float | None], weights: dict[str, float]) -> tuple[float, float]:
    available = {k: float(v) for k, v in values.items() if v is not None and np.isfinite(float(v)) and k in weights}
    if not available:
        return NEUTRAL, 0.0
    used_weight = sum(float(weights[k]) for k in available)
    if used_weight <= 0:
        return NEUTRAL, 0.0
    raw = sum(available[k] * float(weights[k]) for k in available) / used_weight
    total_weight = sum(float(v) for v in weights.values()) or 1.0
    coverage = min(1.0, used_weight / total_weight)
    # Missing families do not disappear from the risk model: shrink to neutral.
    score = NEUTRAL + coverage * (raw - NEUTRAL)
    return _clip(score), float(coverage)


def _normalize_positive_metric(value: float | None, peer_values: pd.Series) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    peers = pd.to_numeric(peer_values, errors="coerce").dropna()
    if len(peers) < 3:
        return NEUTRAL
    return float((peers.rank(pct=True).iloc[-1] if False else (peers <= value).mean()) * 100.0)


def load_config(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class SectorRotationResult:
    sectors: pd.DataFrame
    diagnostic: dict[str, Any]


def _build_sector_base(actions: pd.DataFrame, aliases: dict[str, list[str]]) -> tuple[pd.DataFrame, dict[str, str | None]]:
    work = actions.copy()
    work["_sector"] = _sector_series(work)
    fields: dict[str, str | None] = {}
    values: dict[str, pd.Series] = {}
    for logical, names in aliases.items():
        values[logical], fields[logical] = _first_numeric(work, names)

    above50 = work[fields["above_mm50"]] if fields.get("above_mm50") else pd.Series(pd.NA, index=work.index)
    above200 = work[fields["above_mm200"]] if fields.get("above_mm200") else pd.Series(pd.NA, index=work.index)

    rows: list[dict[str, Any]] = []
    for sector, idx in work.groupby("_sector").groups.items():
        if sector == "NON_CLASSE" or len(idx) < 3:
            continue
        row: dict[str, Any] = {"sector": sector, "n_actions": int(len(idx))}
        for logical, series in values.items():
            if logical in {"above_mm50", "above_mm200"}:
                continue
            row[f"median_{logical}"] = _median(series.loc[idx])
        row["breadth_mm50"] = _bool_pct(above50.loc[idx])
        row["breadth_mm200"] = _bool_pct(above200.loc[idx])
        p1 = row.get("median_perf_1m")
        p3 = row.get("median_perf_3m")
        row["momentum_acceleration"] = (float(p1) - float(p3) / 3.0) if p1 is not None and p3 is not None else None
        perf_series = pd.to_numeric(values.get("perf_1m", pd.Series(np.nan, index=work.index)).loc[idx], errors="coerce")
        row["positive_1m_share"] = float((perf_series > 0).mean() * 100.0) if perf_series.notna().any() else None
        rows.append(row)

    base = pd.DataFrame(rows)
    if base.empty:
        return base, fields

    # Cross-sector ranks provide robust relative normalization without forcing one valuation scale on all industries.
    for metric in (
        "median_perf_1m", "median_perf_3m", "median_perf_6m", "momentum_acceleration",
        "breadth_mm50", "breadth_mm200", "positive_1m_share", "median_volume_ratio",
        "median_revenue_growth", "median_earnings_growth", "median_eps_revision",
    ):
        if metric in base.columns:
            base[f"rank_{metric}"] = _rank(base[metric])

    for metric in ("median_pe", "median_pb", "median_ps", "median_volatility", "median_beta"):
        if metric in base.columns:
            # Higher valuation/volatility = higher risk percentile.
            base[f"risk_rank_{metric}"] = _rank(base[metric])

    market_p1 = _median(values.get("perf_1m", pd.Series(np.nan, index=work.index)))
    base["relative_strength_1m"] = pd.to_numeric(base.get("median_perf_1m"), errors="coerce") - (market_p1 if market_p1 is not None else 0.0)
    base["rank_relative_strength_1m"] = _rank(base["relative_strength_1m"])
    return base, fields


def _row_score(row: pd.Series, cfg: dict[str, Any]) -> dict[str, Any]:
    rls_w = cfg["score_weights"]["RLS"]

    breadth_vals = [row.get("breadth_mm50"), row.get("breadth_mm200")]
    breadth_clean = [float(v) for v in breadth_vals if v is not None and pd.notna(v)]
    breadth = float(np.mean(breadth_clean)) if breadth_clean else None

    rev_rank = row.get("rank_median_revenue_growth")
    earn_rank = row.get("rank_median_earnings_growth")
    growth_ranks = [float(v) for v in (rev_rank, earn_rank) if v is not None and pd.notna(v)]
    fundamental_acc = float(np.mean(growth_ranks)) if growth_ranks else None

    eps_rev = row.get("rank_median_eps_revision")
    rs = row.get("rank_relative_strength_1m")
    accel = row.get("rank_momentum_acceleration")
    early_price = None
    ep = [row.get("rank_median_perf_1m"), row.get("rank_median_volume_ratio")]
    ep = [float(v) for v in ep if v is not None and pd.notna(v)]
    if ep:
        early_price = float(np.mean(ep))

    diffusion = row.get("positive_1m_share")

    # Optional upstream families can be supplied by future collectors without changing the engine contract.
    flows = row.get("sector_flow_score") if pd.notna(row.get("sector_flow_score", np.nan)) else None
    macro = row.get("sector_macro_score") if pd.notna(row.get("sector_macro_score", np.nan)) else None
    catalysts = row.get("sector_catalyst_score") if pd.notna(row.get("sector_catalyst_score", np.nan)) else None

    rls_components = {
        "earnings_revisions": float(eps_rev) if eps_rev is not None and pd.notna(eps_rev) else None,
        "breadth": breadth,
        "relative_strength": float(rs) if rs is not None and pd.notna(rs) else None,
        "flows": flows,
        "fundamental_acceleration": fundamental_acc,
        "macro_compatibility": macro,
        "catalysts": catalysts,
        "early_price_volume": early_price,
        "internal_diffusion": float(diffusion) if diffusion is not None and pd.notna(diffusion) else None,
    }
    rls, rls_coverage = _weighted_available(rls_components, rls_w)

    sqs_inputs = [breadth, fundamental_acc, float(eps_rev) if eps_rev is not None and pd.notna(eps_rev) else None]
    sqs_clean = [v for v in sqs_inputs if v is not None and np.isfinite(v)]
    sqs = _clip(float(np.mean(sqs_clean)) if sqs_clean else NEUTRAL)

    cts_inputs = [row.get("rank_median_perf_1m"), accel, rs, breadth]
    cts_clean = [float(v) for v in cts_inputs if v is not None and pd.notna(v)]
    cts = _clip(float(np.mean(cts_clean)) if cts_clean else NEUTRAL)

    # STS remains deliberately conservative until long-horizon sector-specific collectors are present.
    structural = row.get("sector_structural_score") if pd.notna(row.get("sector_structural_score", np.nan)) else None
    sts = _clip(float(structural) if structural is not None else NEUTRAL)

    mcs_inputs = [row.get("rank_median_perf_1m"), rs, breadth, diffusion]
    mcs_clean = [float(v) for v in mcs_inputs if v is not None and pd.notna(v)]
    mcs = _clip(float(np.mean(mcs_clean)) if mcs_clean else NEUTRAL)

    valuation_ranks = [row.get("risk_rank_median_pe"), row.get("risk_rank_median_pb"), row.get("risk_rank_median_ps")]
    valuation_clean = [float(v) for v in valuation_ranks if v is not None and pd.notna(v)]
    valuation_vs_market = float(np.mean(valuation_clean)) if valuation_clean else None
    valuation_vs_history = row.get("sector_valuation_history_percentile") if pd.notna(row.get("sector_valuation_history_percentile", np.nan)) else None

    perf3 = row.get("rank_median_perf_3m")
    growth = fundamental_acc
    if perf3 is not None and pd.notna(perf3) and growth is not None:
        price_fund_gap = _clip(50.0 + (float(perf3) - float(growth)) * 0.75)
    else:
        price_fund_gap = None

    perf1_rank = row.get("rank_median_perf_1m")
    dist = row.get("median_distance_high_52w")
    near_high = None
    if dist is not None and pd.notna(dist):
        # distance_high_52w_pct in this project is expected as a positive distance from the high.
        near_high = _clip(100.0 - min(100.0, max(0.0, float(dist)) * 5.0))
    tech_parts = [float(v) for v in (perf1_rank, near_high) if v is not None and pd.notna(v)]
    technical_overextension = float(np.mean(tech_parts)) if tech_parts else None

    crowd_parts = [row.get("rank_median_volume_ratio"), row.get("rank_median_perf_1m")]
    crowd_clean = [float(v) for v in crowd_parts if v is not None and pd.notna(v)]
    crowding = float(np.mean(crowd_clean)) if crowd_clean else None

    breadth_divergence = None
    if perf1_rank is not None and pd.notna(perf1_rank) and breadth is not None:
        breadth_divergence = _clip(50.0 + (float(perf1_rank) - float(breadth)) * 0.8)

    multiple_dependency = price_fund_gap
    expectation_fragility = None
    if valuation_vs_market is not None:
        frag_parts = [valuation_vs_market, technical_overextension, crowding]
        frag_clean = [float(v) for v in frag_parts if v is not None and np.isfinite(v)]
        expectation_fragility = float(np.mean(frag_clean)) if frag_clean else None

    vol_rank = row.get("risk_rank_median_volatility")
    avcr_components = {
        "valuation_vs_history": float(valuation_vs_history) if valuation_vs_history is not None else None,
        "valuation_vs_market": valuation_vs_market,
        "price_fundamental_gap": price_fund_gap,
        "technical_overextension": technical_overextension,
        "crowding": crowding,
        "breadth_divergence": breadth_divergence,
        "multiple_expansion_dependency": multiple_dependency,
        "expectation_fragility": expectation_fragility,
        "volatility_regime": float(vol_rank) if vol_rank is not None and pd.notna(vol_rank) else None,
    }
    raw_vcr, vcr_coverage = _weighted_available(avcr_components, cfg["score_weights"]["AVCR"])

    justification_inputs = [fundamental_acc, float(eps_rev) if eps_rev is not None and pd.notna(eps_rev) else None, breadth]
    justification_clean = [float(v) for v in justification_inputs if v is not None and np.isfinite(v)]
    valuation_justification = _clip(float(np.mean(justification_clean)) if justification_clean else NEUTRAL)
    avcr = _clip(raw_vcr - 0.35 * (valuation_justification - NEUTRAL))
    margin_safety = _clip(100.0 - avcr)

    # Data quality reflects real usable families; optional unimplemented families are visible instead of silently imputed.
    essential = {
        "momentum": row.get("median_perf_1m"),
        "medium_momentum": row.get("median_perf_3m"),
        "breadth50": row.get("breadth_mm50"),
        "breadth200": row.get("breadth_mm200"),
        "earnings_revision": row.get("median_eps_revision"),
        "revenue_growth": row.get("median_revenue_growth"),
        "earnings_growth": row.get("median_earnings_growth"),
        "valuation": valuation_vs_market,
        "volume": row.get("median_volume_ratio"),
    }
    completeness = 100.0 * sum(v is not None and pd.notna(v) for v in essential.values()) / len(essential)
    source_reliability = 85.0  # internal PIT calculations and existing governed master fields
    pit_quality = 90.0
    freshness = 90.0
    coverage = _clip(min(100.0, float(row.get("n_actions", 0)) / 20.0 * 100.0))
    dqs_weights = cfg["score_weights"]["DQS"]
    dqs = _clip(
        dqs_weights["freshness"] * freshness
        + dqs_weights["completeness"] * completeness
        + dqs_weights["pit_quality"] * pit_quality
        + dqs_weights["source_reliability"] * source_reliability
        + dqs_weights["sector_coverage"] * coverage
    )

    confluence = row.get("theme_confluence_score") if pd.notna(row.get("theme_confluence_score", np.nan)) else NEUTRAL
    opportunity_values = {
        "RLS": rls,
        "SQS": sqs,
        "CTS": cts,
        "STS": sts,
        "MCS": mcs,
        "margin_of_safety": margin_safety,
        "theme_confluence": float(confluence),
    }
    ow = cfg["score_weights"]["RARS_OPPORTUNITY"]
    opportunity = sum(float(opportunity_values[k]) * float(ow[k]) for k in ow)
    risk_adjustment = 1.0 - 0.35 * avcr / 100.0
    dqs_adjustment = 0.70 + 0.30 * dqs / 100.0
    rars = _clip(opportunity * risk_adjustment * dqs_adjustment)

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
        "margin_of_safety": round(margin_safety, 4),
        "DQS": round(dqs, 4),
        "RARS": round(rars, 4),
        "technical_overextension": None if technical_overextension is None else round(float(technical_overextension), 4),
        "crowding": None if crowding is None else round(float(crowding), 4),
        "breadth_divergence": None if breadth_divergence is None else round(float(breadth_divergence), 4),
        "price_fundamental_gap": None if price_fund_gap is None else round(float(price_fund_gap), 4),
        "expectation_fragility": None if expectation_fragility is None else round(float(expectation_fragility), 4),
        "rls_components": rls_components,
        "avcr_components": avcr_components,
        "data_completeness_pct": round(completeness, 4),
    }


def _prior_for_sector(history: pd.DataFrame | None, sector: str) -> pd.Series | None:
    if history is None or history.empty or "sector" not in history.columns:
        return None
    h = history.loc[history["sector"].astype(str) == str(sector)].copy()
    if h.empty:
        return None
    if "as_of" in h.columns:
        h["_as_of"] = pd.to_datetime(h["as_of"], errors="coerce", utc=True)
        h = h.sort_values("_as_of")
    return h.iloc[-1]


def _state_and_warnings(row: dict[str, Any], prior: pd.Series | None, cfg: dict[str, Any]) -> dict[str, Any]:
    rls = float(row["RLS"])
    sqs = float(row["SQS"])
    mcs = float(row["MCS"])
    avcr = float(row["AVCR"])
    dqs = float(row["DQS"])
    th = cfg["state_thresholds"]
    val = cfg["valuation_thresholds"]
    wr = cfg["warning_rules"]

    prior_rls = float(prior.get("RLS")) if prior is not None and pd.notna(prior.get("RLS")) else None
    prior_breadth = float(prior.get("breadth_score")) if prior is not None and pd.notna(prior.get("breadth_score")) else None
    velocity = rls - prior_rls if prior_rls is not None else 0.0
    breadth_score = float(row.get("breadth_score", NEUTRAL))
    breadth_delta = breadth_score - prior_breadth if prior_breadth is not None else 0.0

    if rls < th["rotation_out_rls"] and velocity <= 0:
        state = "ROTATION_OUT"
    elif rls < th["distribution_rls"] and velocity < 0:
        state = "DISTRIBUTION"
    elif rls >= th["leadership_rls"] and sqs >= th["leadership_sqs"] and mcs >= th["leadership_mcs"]:
        state = "LEADERSHIP"
    elif rls >= th["confirmed_rotation_rls"] and mcs >= th["confirmed_rotation_mcs"]:
        state = "CONFIRMED_ROTATION"
    elif rls >= th["early_rotation_enter"] and velocity >= 0:
        state = "EARLY_ROTATION"
    elif rls >= 55.0 and velocity >= 0:
        state = "ACCUMULATION"
    else:
        state = "NEUTRAL"

    warnings: list[str] = []
    families: dict[str, bool] = {}
    if rls >= val["promising_but_overvalued_rls_min"] and avcr >= val["promising_but_overvalued_avcr_min"]:
        warnings.append("PROMISING_BUT_OVERVALUED")
        families["valuation"] = True
    tech = row.get("technical_overextension")
    if tech is not None and float(tech) >= wr["technical_overextension_rank_min"]:
        warnings.append("TECHNICAL_OVEREXTENSION")
        families["technical"] = True
    crowd = row.get("crowding")
    if crowd is not None and float(crowd) >= wr["crowding_rank_min"]:
        warnings.append("CROWDING_EUPHORIA")
        families["crowding"] = True
    if breadth_delta <= -float(wr["leadership_narrowing_breadth_drop_pp"]):
        warnings.append("LEADERSHIP_NARROWING")
        families["breadth"] = True
    pfg = row.get("price_fundamental_gap")
    if pfg is not None and float(pfg) >= 75.0:
        warnings.append("MULTIPLE_EXPANSION_DEPENDENCY")
        families["valuation"] = True
    if avcr >= wr["perfection_priced_in_avcr_min"] and float(row.get("valuation_justification", NEUTRAL)) < 60.0:
        warnings.append("PERFECTION_PRICED_IN")
        families["valuation"] = True
    if avcr <= wr["value_trap_valuation_risk_max"] and rls <= wr["value_trap_rls_max"]:
        warnings.append("VALUE_TRAP")
        families["fundamentals"] = True
    if velocity < -8.0:
        families["rotation"] = True
    if mcs < 45.0:
        families["market_confirmation"] = True
    if sqs < 45.0:
        families["fundamentals"] = True

    correction_alert = avcr >= 65.0 and sum(bool(v) for v in families.values()) >= int(wr["correction_alert_min_independent_families"])
    if correction_alert:
        warnings.append("CORRECTION_ALERT")

    if state == "LEADERSHIP" and avcr >= 65.0:
        warnings.append("BULLISH_BUT_OVEREXTENDED")

    # Confidence cannot exceed data quality.
    warning_confidence = min(dqs, 50.0 + 10.0 * len(set(families))) if warnings else 0.0

    dec = cfg["decision_thresholds"]
    min_decision_dqs = cfg["governance"]["minimum_dqs_for_decision"]
    if dqs < cfg["governance"]["minimum_dqs_for_signal"]:
        new_action = "NO_ACTION_INSUFFICIENT_DATA"
    elif correction_alert:
        new_action = "NO_NEW_ENTRY"
    elif avcr >= dec["no_chase_avcr_min"] and rls >= dec["buy_rls_min"]:
        new_action = "NO_CHASE"
    elif rls >= dec["buy_rls_min"] and avcr <= dec["wait_pullback_avcr_max"] and avcr > dec["accumulate_avcr_max"]:
        new_action = "WAIT_FOR_PULLBACK"
    elif rls >= dec["buy_rls_min"] and avcr <= dec["accumulate_avcr_max"] and avcr > dec["buy_avcr_max"]:
        new_action = "ACCUMULATE_ON_WEAKNESS"
    elif dqs >= min_decision_dqs and rls >= dec["priority_buy_rls_min"] and sqs >= dec["priority_buy_sqs_min"] and mcs >= dec["priority_buy_mcs_min"] and avcr <= dec["priority_buy_avcr_max"]:
        new_action = "PRIORITY_BUY_ZONE"
    elif dqs >= min_decision_dqs and rls >= dec["buy_rls_min"] and avcr <= dec["buy_avcr_max"]:
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

    return {
        "state": state,
        "warnings": sorted(set(warnings)),
        "warning_confidence": round(float(warning_confidence), 4),
        "independent_warning_families": int(sum(bool(v) for v in families.values())),
        "correction_alert": bool(correction_alert),
        "RLS_velocity": round(float(velocity), 4),
        "breadth_delta": round(float(breadth_delta), 4),
        "new_position_action": new_action,
        "existing_position_action": existing_action,
    }


def build_sector_rotation_v2(
    actions: pd.DataFrame,
    config: dict[str, Any],
    *,
    history: pd.DataFrame | None = None,
    as_of: str | None = None,
) -> SectorRotationResult:
    """Build an explainable sector-rotation shadow snapshot.

    This function is intentionally decision-isolated: it never mutates action or ETF scores.
    Missing factor families shrink scores toward neutral and reduce DQS rather than being
    silently imputed as positive evidence. Historical state is optional and only used for
    velocity/breadth-delta warnings.
    """
    if actions.empty:
        return SectorRotationResult(pd.DataFrame(), {"status": "EMPTY", "version": config.get("version")})

    aliases = config.get("field_aliases", {})
    base, field_resolution = _build_sector_base(actions, aliases)
    if base.empty:
        return SectorRotationResult(pd.DataFrame(), {"status": "NO_SECTORS", "version": config.get("version")})

    scored_rows: list[dict[str, Any]] = []
    snapshot_date = as_of or datetime.now(timezone.utc).date().isoformat()
    for _, r in base.iterrows():
        base_dict = r.to_dict()
        score = _row_score(r, config)
        breadth_vals = [r.get("breadth_mm50"), r.get("breadth_mm200")]
        breadth_clean = [float(v) for v in breadth_vals if v is not None and pd.notna(v)]
        score["breadth_score"] = round(float(np.mean(breadth_clean)) if breadth_clean else NEUTRAL, 4)
        prior = _prior_for_sector(history, str(r["sector"]))
        state = _state_and_warnings({**base_dict, **score}, prior, config)
        scored_rows.append({
            **base_dict,
            **{k: v for k, v in score.items() if k not in {"rls_components", "avcr_components"}},
            **state,
            "rls_components": json.dumps(score["rls_components"], ensure_ascii=False, sort_keys=True),
            "avcr_components": json.dumps(score["avcr_components"], ensure_ascii=False, sort_keys=True),
            "as_of": snapshot_date,
            "model_version": config.get("version", "SECTOR_ROTATION_V2"),
            "mode": config.get("mode", "SHADOW_ONLY"),
        })

    sectors = pd.DataFrame(scored_rows).sort_values(["RARS", "RLS"], ascending=[False, False]).reset_index(drop=True)
    sectors["rank"] = np.arange(1, len(sectors) + 1)

    diag = {
        "status": "OK",
        "version": config.get("version"),
        "mode": config.get("mode"),
        "as_of": snapshot_date,
        "sector_count": int(len(sectors)),
        "field_resolution": field_resolution,
        "average_DQS": round(float(sectors["DQS"].mean()), 4),
        "average_RLS": round(float(sectors["RLS"].mean()), 4),
        "average_AVCR": round(float(sectors["AVCR"].mean()), 4),
        "promising_but_overvalued": sectors.loc[sectors["warnings"].apply(lambda x: "PROMISING_BUT_OVERVALUED" in x), "sector"].tolist(),
        "correction_alerts": sectors.loc[sectors["correction_alert"], "sector"].tolist(),
        "priority_candidates": sectors.loc[sectors["new_position_action"].eq("PRIORITY_BUY_ZONE"), "sector"].tolist(),
        "governance": config.get("governance", {}),
    }
    return SectorRotationResult(sectors=sectors, diagnostic=diag)


def append_history(snapshot: pd.DataFrame, path: str | Path) -> None:
    """Append one row per sector/date without duplicating the same model snapshot."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        old = pd.read_csv(p, sep=";", encoding="utf-8-sig")
        combined = pd.concat([old, snapshot], ignore_index=True, sort=False)
    else:
        combined = snapshot.copy()
    keys = [c for c in ("sector", "as_of", "model_version") if c in combined.columns]
    if keys:
        combined = combined.drop_duplicates(keys, keep="last")
    combined.to_csv(p, sep=";", index=False, encoding="utf-8-sig")
