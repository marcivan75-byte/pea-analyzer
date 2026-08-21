from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from v182.features.action_decision_enhancements import build_action_enhancement_observations
from v182.features.sector_rotation import build_rotation_observations


def _finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in {"", "nan", "none", "na", "n/a", "unknown"}:
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
    if actions.empty or "isin" not in actions.columns:
        return {}
    minimum = int(cfg["data_policy"].get("cross_sectional_context_requires_min_actions", 20))
    if len(actions) < minimum:
        return {}
    work = actions.copy()
    weights = cfg["context_overlay"]["relative_strength_weights"]
    candidates = {
        "perf_1m_rank": "perf_1m_pct",
        "perf_3m_rank": "perf_3m_pct",
        "perf_6m_rank": "perf_6m_pct",
    }
    ranks: dict[str, pd.Series] = {}
    for key, field in candidates.items():
        ranks[key] = _rank_score(work[field]) if field in work.columns else pd.Series(np.nan, index=work.index, dtype=float)
    output: dict[str, float] = {}
    for idx, row in work.iterrows():
        num = 0.0
        den = 0.0
        for key, weight in weights.items():
            value = _finite(ranks[key].loc[idx])
            if value is None:
                continue
            num += float(weight) * value
            den += float(weight)
        if den <= 0:
            continue
        isin = str(row.get("isin") or "").upper()
        if isin:
            output[isin] = float(np.clip(num / den, 0.0, 100.0))
    return output


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
        observations, sectors, rotation_diag = build_rotation_observations(actions)
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

    diagnostics["mapped_actions"] = int(len(overlay))
    diagnostics["fields_generated"] = sorted({field for values in overlay.values() for field in values})
    return dict(overlay), diagnostics


def merge_action_ct_context(row: pd.Series | dict[str, Any], derived: dict[str, Any], cfg: dict) -> dict[str, Any]:
    """Merge current master row and derived fallback context without overwriting observations."""
    base = dict(row)
    prefer_existing = bool(cfg["context_overlay"].get("prefer_existing_observed_value_over_derived_fallback", True))
    for field, value in derived.items():
        if not prefer_existing or field not in base or _missing(base.get(field)):
            base[field] = value
    return base
