from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from v182.features.action_decision_enhancements import build_action_enhancement_observations
from v182.features.sector_rotation import build_rotation_observations


MISSING_TEXT = {"", "nan", "none", "na", "n/a", "unknown"}


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in MISSING_TEXT:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _rank_score(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() < 3:
        return pd.Series(np.nan, index=series.index, dtype=float)
    return values.rank(method="average", pct=True) * 100.0


def _cross_sectional_relative_strength(actions: pd.DataFrame, cfg: dict) -> dict[str, float]:
    """Vectorized current-snapshot relative-strength score.

    Missing horizons do not receive a neutral value: weights are renormalized
    row-by-row over observed ranks only.
    """
    if actions.empty or "isin" not in actions.columns:
        return {}
    minimum = int(cfg["data_policy"].get("cross_sectional_context_requires_min_actions", 20))
    if len(actions) < minimum:
        return {}

    weights = cfg["context_overlay"]["relative_strength_weights"]
    field_map = {
        "perf_1m_rank": "perf_1m_pct",
        "perf_3m_rank": "perf_3m_pct",
        "perf_6m_rank": "perf_6m_pct",
    }
    ranks = pd.DataFrame(index=actions.index)
    for key, field in field_map.items():
        ranks[key] = _rank_score(actions[field]) if field in actions.columns else np.nan

    weight_series = pd.Series({key: float(value) for key, value in weights.items()}, dtype=float)
    available_weight = ranks.notna().mul(weight_series, axis=1).sum(axis=1)
    numerator = ranks.mul(weight_series, axis=1).sum(axis=1, skipna=True)
    score = (numerator / available_weight.where(available_weight > 0)).clip(0.0, 100.0)

    isin = actions["isin"].astype(str).str.upper().str.strip()
    valid = score.notna() & ~isin.str.lower().isin(MISSING_TEXT)
    return dict(zip(isin.loc[valid], score.loc[valid].astype(float), strict=False))


def _coverage_diagnostics(actions: pd.DataFrame, overlay: dict[str, dict[str, Any]]) -> dict[str, Any]:
    total = int(len(actions))
    if total == 0:
        return {"action_rows": 0, "field_coverage_pct": {}, "context_richness_pct": 0.0}
    fields = sorted({field for values in overlay.values() for field in values})
    coverage: dict[str, float] = {}
    for field in fields:
        count = sum(1 for values in overlay.values() if not _missing(values.get(field)))
        coverage[field] = round(100.0 * count / total, 4)
    quality_fields = {"morningstar_action_score", "target_upside_growth_score", "target_upside_gt4_score"}
    theme_fields = {"theme_rotation_exposure_score", "theme_risk_adjusted_score", "theme_confluence_score", "sector_macro_score"}
    rich = sum(
        1
        for values in overlay.values()
        if any(not _missing(values.get(field)) for field in quality_fields | theme_fields)
    )
    return {
        "action_rows": total,
        "field_coverage_pct": coverage,
        "context_richness_pct": round(100.0 * rich / total, 4),
    }


def build_action_ct_context_overlay(actions: pd.DataFrame, cfg: dict) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Build current-snapshot CT context from governed observed/derived features.

    Existing observed master values always win. Derived values are fallbacks only.
    No historical reconstruction is performed here; this is a current-run overlay.
    """
    overlay: dict[str, dict[str, Any]] = defaultdict(dict)
    diagnostics: dict[str, Any] = {
        "status": "OK",
        "action_rows": int(len(actions)),
        "sector_rotation": {},
        "action_enhancements": {},
        "cross_sectional_relative_strength": {},
        "decision_influence": 0.0,
    }
    if actions.empty or "isin" not in actions.columns:
        diagnostics["status"] = "NO_ACTION_MASTER"
        return {}, diagnostics

    if bool(cfg["context_overlay"].get("build_sector_rotation_each_run", True)):
        observations, sectors, rotation_diag = build_rotation_observations(actions, cfg=cfg)
        for obs in observations:
            isin = str(obs.get("isin") or "").upper()
            field = str(obs.get("field") or "")
            if isin and field:
                overlay[isin][field] = obs.get("value")
        diagnostics["sector_rotation"] = {
            **rotation_diag,
            "observations": int(len(observations)),
            "sector_rows": int(len(sectors)),
        }

    if bool(cfg["context_overlay"].get("build_action_enhancements_each_run", True)):
        enhancements = build_action_enhancement_observations(actions)
        for obs in enhancements:
            isin = str(obs.get("isin") or "").upper()
            field = str(obs.get("field") or "")
            if isin and field:
                overlay[isin][field] = obs.get("value")
        diagnostics["action_enhancements"] = {"observations": int(len(enhancements))}

    if bool(cfg["context_overlay"].get("build_cross_sectional_relative_strength_each_run", True)):
        rel = _cross_sectional_relative_strength(actions, cfg)
        for isin, value in rel.items():
            overlay[isin]["relative_strength"] = value
        diagnostics["cross_sectional_relative_strength"] = {"mapped_actions": int(len(rel))}

    result = dict(overlay)
    diagnostics["mapped_actions"] = int(len(result))
    diagnostics["fields_generated"] = sorted({field for values in result.values() for field in values})
    diagnostics["coverage"] = _coverage_diagnostics(actions, result)
    return result, diagnostics


def merge_action_ct_context(row: pd.Series | dict[str, Any], derived: dict[str, Any], cfg: dict) -> dict[str, Any]:
    """Merge current master row and derived fallback context without overwriting observations."""
    base = dict(row)
    prefer_existing = bool(cfg["context_overlay"].get("prefer_existing_observed_value_over_derived_fallback", True))
    for field, value in derived.items():
        if not prefer_existing or field not in base or _missing(base.get(field)):
            base[field] = value
    return base
