from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import json
import math

import pandas as pd

SUPPORTED_HORIZONS = ("CT", "LT")
UNSUPPORTED_CATEGORICAL_FIELDS = {"distribution_policy"}


def load_json(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return payload


def _validate_contract(
    registry: Mapping,
    shadow_cfg: Mapping,
    horizon: str,
) -> tuple[dict, dict, dict, float]:
    if horizon not in SUPPORTED_HORIZONS:
        raise ValueError(f"ETF_SHADOW_UNSUPPORTED_HORIZON:{horizon}")

    governance = shadow_cfg.get("governance") or {}
    scoring = shadow_cfg.get("scoring") or {}
    validation = shadow_cfg.get("validation") or {}
    if shadow_cfg.get("mode") != "SHADOW_ONLY":
        raise ValueError("ETF_CT_LT_MUST_REMAIN_SHADOW_ONLY")
    if governance.get("real_orders_allowed") is not False:
        raise ValueError("ETF_CT_LT_REAL_ORDERS_FORBIDDEN")
    if governance.get("can_create_buy") is not False or governance.get("can_create_sell") is not False:
        raise ValueError("ETF_CT_LT_BUY_SELL_INFLUENCE_FORBIDDEN")
    if governance.get("promotion_allowed") is not False:
        raise ValueError("ETF_CT_LT_PROMOTION_FORBIDDEN")
    if float(governance.get("decision_influence", 1.0)) != 0.0:
        raise ValueError("ETF_CT_LT_DECISION_INFLUENCE_MUST_BE_ZERO")
    if float(governance.get("mt_reference_score_influence", 1.0)) != 0.0:
        raise ValueError("ETF_CT_LT_MT_INFLUENCE_MUST_BE_ZERO")
    if governance.get("t1_t2_forbidden") is not True:
        raise ValueError("ETF_CT_LT_T1_T2_MUST_REMAIN_FORBIDDEN")
    if scoring.get("neutral_imputation") is not False:
        raise ValueError("ETF_CT_LT_NEUTRAL_IMPUTATION_FORBIDDEN")
    if scoring.get("categorical_numeric_inference") is not False:
        raise ValueError("ETF_CT_LT_CATEGORICAL_INFERENCE_FORBIDDEN")
    if validation.get("dedicated_pit_oos_required_before_promotion") is not True:
        raise ValueError("ETF_CT_LT_PIT_OOS_VALIDATION_REQUIRED")
    if float(validation.get("stress_calibration_weight", 1.0)) != 0.0:
        raise ValueError("ETF_CT_LT_STRESS_WEIGHT_MUST_REMAIN_ZERO")

    registry_governance = registry.get("governance") or {}
    if registry_governance.get("t1_t2_forbidden") is not True:
        raise ValueError("ETF_REGISTRY_T1_T2_CONTRACT_DRIFT")

    weights = dict((registry.get("weights") or {}).get(horizon) or {})
    directions = dict((registry.get("directions") or {}).get(horizon) or {})
    if not weights or set(weights) != set(directions):
        raise ValueError(f"ETF_{horizon}_WEIGHT_DIRECTION_CONTRACT_DRIFT")
    weight_total = sum(float(value) for value in weights.values())
    if not math.isclose(weight_total, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError(f"ETF_{horizon}_WEIGHT_TOTAL_DRIFT:{weight_total}")

    horizon_cfg = dict((shadow_cfg.get("horizons") or {}).get(horizon) or {})
    minimum = float(horizon_cfg.get("minimum_weighted_coverage", -1.0))
    if not 0.0 < minimum <= 1.0:
        raise ValueError(f"ETF_{horizon}_INVALID_MINIMUM_COVERAGE")

    cross_section_fraction = float(scoring.get("minimum_cross_section_observed_fraction", -1.0))
    if not 0.0 < cross_section_fraction <= 1.0:
        raise ValueError("ETF_CT_LT_INVALID_CROSS_SECTION_FRACTION")
    return weights, directions, horizon_cfg, cross_section_fraction


def _numeric_series(frame: pd.DataFrame, field: str) -> pd.Series:
    if field not in frame.columns or field in UNSUPPORTED_CATEGORICAL_FIELDS:
        return pd.Series(float("nan"), index=frame.index, dtype=float)
    values = pd.to_numeric(frame[field], errors="coerce").astype(float)
    return values.where(values.map(lambda value: math.isfinite(value) if pd.notna(value) else False))


def _percentile_score(values: pd.Series, direction: str) -> pd.Series:
    observed = values.dropna()
    result = pd.Series(float("nan"), index=values.index, dtype=float)
    if observed.empty:
        return result
    if direction == "HIGH":
        result.loc[observed.index] = observed.rank(method="average", pct=True, ascending=True) * 100.0
    elif direction == "LOW":
        result.loc[observed.index] = observed.rank(method="average", pct=True, ascending=False) * 100.0
    else:
        raise ValueError(f"ETF_CT_LT_UNSUPPORTED_DIRECTION:{direction}")
    return result


def score_horizon_shadow(
    etfs: pd.DataFrame,
    registry: Mapping,
    shadow_cfg: Mapping,
    horizon: str,
) -> tuple[pd.DataFrame, dict]:
    """Score a current ETF snapshot without creating a production decision.

    Only observed numeric values participate. Missing or insufficiently populated
    cross-sectional criteria contribute neither score nor denominator. Available
    weights are renormalized solely for a SHADOW diagnostic. Categorical fields
    are never guessed into numeric values.
    """
    weights, directions, horizon_cfg, cross_section_fraction = _validate_contract(
        registry, shadow_cfg, horizon
    )
    if "isin" not in etfs.columns:
        raise ValueError("ETF_CT_LT_ISIN_REQUIRED")
    if etfs["isin"].astype(str).duplicated().any():
        raise ValueError("ETF_CT_LT_DUPLICATE_ISIN")
    if etfs.empty:
        raise ValueError("ETF_CT_LT_EMPTY_UNIVERSE")

    base = etfs.copy().reset_index(drop=True)
    minimum_cross_section_observations = max(2, math.ceil(len(base) * cross_section_fraction))
    criterion_scores = pd.DataFrame(index=base.index)
    observed_for_score = pd.DataFrame(index=base.index)
    cross_section_counts: dict[str, int] = {}
    rankable_criteria: list[str] = []
    blocked_criteria: list[str] = []

    for field in weights:
        values = _numeric_series(base, field)
        observed_count = int(values.notna().sum())
        cross_section_counts[field] = observed_count
        rankable = observed_count >= minimum_cross_section_observations
        if rankable:
            rankable_criteria.append(field)
            score = _percentile_score(values, str(directions[field]).upper())
        else:
            blocked_criteria.append(field)
            score = pd.Series(float("nan"), index=base.index, dtype=float)
        criterion_scores[field] = score
        observed_for_score[field] = score.notna()

    numerator = pd.Series(0.0, index=base.index, dtype=float)
    denominator = pd.Series(0.0, index=base.index, dtype=float)
    for field, weight in weights.items():
        numeric_weight = float(weight)
        score = criterion_scores[field]
        ok = score.notna()
        numerator = numerator + score.fillna(0.0) * numeric_weight
        denominator = denominator + ok.astype(float) * numeric_weight

    coverage = denominator
    minimum = float(horizon_cfg["minimum_weighted_coverage"])
    scorable = coverage >= minimum
    shadow_score = (numerator / denominator.where(denominator > 0)).where(scorable)
    high_threshold = float(horizon_cfg["legacy_high_score_threshold"])
    watch_threshold = float(horizon_cfg["legacy_watch_threshold"])

    out = pd.DataFrame(
        {
            "isin": base["isin"].astype(str),
            "name": base["name"] if "name" in base.columns else base["isin"].astype(str),
            "horizon": horizon,
            "shadow_score": shadow_score.round(6),
            "weighted_coverage": coverage.round(6),
            "observed_criteria": observed_for_score.sum(axis=1).astype(int),
            "configured_criteria": len(weights),
            "rankable_cross_section_criteria": len(rankable_criteria),
        }
    )
    out["shadow_context"] = "DATA_INSUFFICIENT"
    out.loc[scorable, "shadow_context"] = "LOW_SCORE_CONTEXT"
    out.loc[scorable & shadow_score.ge(watch_threshold), "shadow_context"] = "WATCH_CONTEXT"
    out.loc[scorable & shadow_score.ge(high_threshold), "shadow_context"] = "HIGH_SCORE_CONTEXT"
    out["decision_role"] = "SHADOW_DIAGNOSTIC_ONLY"
    out["historical_performance_attribution"] = str(horizon_cfg["historical_performance_attribution"])
    out["neutral_imputation_used"] = False
    out["t1_t2_enabled"] = False
    out["promotion_allowed"] = False
    out["real_orders_allowed"] = False
    out["live_orders_enabled"] = False
    out["decision_influence"] = 0.0
    out["mt_reference_score_influence"] = 0.0

    rankable_rows = out["shadow_score"].notna()
    out["shadow_rank"] = pd.NA
    if rankable_rows.any():
        out.loc[rankable_rows, "shadow_rank"] = out.loc[rankable_rows, "shadow_score"].rank(
            method="min", ascending=False
        ).astype("Int64")

    summary = {
        "version": shadow_cfg.get("version"),
        "horizon": horizon,
        "mode": "SHADOW_ONLY",
        "universe_rows": int(len(out)),
        "scorable_rows": int(scorable.sum()),
        "blocked_rows": int((~scorable).sum()),
        "configured_criteria": int(len(weights)),
        "configured_weight_total": float(sum(float(v) for v in weights.values())),
        "minimum_weighted_coverage": minimum,
        "minimum_cross_section_observed_fraction": cross_section_fraction,
        "minimum_cross_section_observations": minimum_cross_section_observations,
        "cross_section_observed_counts": cross_section_counts,
        "rankable_cross_section_criteria": rankable_criteria,
        "blocked_cross_section_criteria": blocked_criteria,
        "legacy_threshold_role": "CONTEXT_BANDS_ONLY_NOT_BUY_RULES",
        "unsupported_categorical_fields": sorted(set(weights) & UNSUPPORTED_CATEGORICAL_FIELDS),
        "historical_performance_attribution": str(horizon_cfg["historical_performance_attribution"]),
        "neutral_imputation_used": False,
        "t1_t2_forbidden": True,
        "promotion_allowed": False,
        "real_orders_allowed": False,
        "decision_influence": 0.0,
        "mt_reference_score_influence": 0.0,
    }
    return out.sort_values(["shadow_rank", "isin"], na_position="last").reset_index(drop=True), summary


def score_ct_lt_shadow(
    etfs: pd.DataFrame,
    registry: Mapping,
    shadow_cfg: Mapping,
) -> tuple[pd.DataFrame, dict]:
    outputs: list[pd.DataFrame] = []
    summaries: dict[str, dict] = {}
    for horizon in SUPPORTED_HORIZONS:
        scored, summary = score_horizon_shadow(etfs, registry, shadow_cfg, horizon)
        outputs.append(scored)
        summaries[horizon] = summary
    combined = pd.concat(outputs, ignore_index=True)
    validation = shadow_cfg.get("validation") or {}
    return combined, {
        "version": shadow_cfg.get("version"),
        "mode": "SHADOW_ONLY",
        "horizons": summaries,
        "decision_influence": 0.0,
        "mt_reference_score_influence": 0.0,
        "real_orders_allowed": False,
        "t1_t2_forbidden": True,
        "current_snapshot_only": True,
        "v21_13_history_depth_gates_apply_to_future_validation": bool(
            validation.get("v21_13_history_depth_gates_apply_to_future_validation")
        ),
        "stress_calibration_weight": float(validation.get("stress_calibration_weight", 0.0)),
        "historical_performance_attribution": "NONE_FOR_CT_LT_UNTIL_DEDICATED_PIT_OOS_VALIDATION",
    }
