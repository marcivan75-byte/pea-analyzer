from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REQUIRED_MAPPING_COLUMNS = {
    "universe",
    "isin",
    "theme_id",
    "exposure_pct",
    "effective_from",
    "effective_to",
    "confidence_pct",
    "source",
    "status",
}


def load_instrument_theme_mapping(path: str | Path, *, as_of: str) -> pd.DataFrame:
    mapping = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False)
    missing = REQUIRED_MAPPING_COLUMNS - set(mapping.columns)
    if missing:
        raise ValueError(f"MISSING_MAPPING_COLUMNS:{sorted(missing)}")
    if mapping.empty:
        return mapping
    mapping["exposure_pct"] = pd.to_numeric(mapping["exposure_pct"], errors="coerce")
    mapping["confidence_pct"] = pd.to_numeric(mapping["confidence_pct"], errors="coerce")
    mapping["effective_from"] = pd.to_datetime(mapping["effective_from"], errors="coerce", utc=True)
    mapping["effective_to"] = pd.to_datetime(mapping["effective_to"], errors="coerce", utc=True)
    date = pd.to_datetime(as_of, errors="raise", utc=True)
    active = mapping["effective_from"].isna() | (mapping["effective_from"] <= date)
    active &= mapping["effective_to"].isna() | (mapping["effective_to"] >= date)
    mapping = mapping.loc[active].copy()
    invalid = mapping["exposure_pct"].notna() & ~mapping["exposure_pct"].between(0, 100)
    if invalid.any():
        raise ValueError("INVALID_EXPOSURE_PCT")
    return mapping


def score_instrument_theme_exposure(
    mapping: pd.DataFrame,
    theme_scores: pd.DataFrame,
    *,
    as_of: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Score effective-dated Action/ETF theme exposures without altering base decisions."""
    if mapping.empty:
        return pd.DataFrame(), {"status": "NO_MAPPING", "mapped_instruments": 0, "decision_influence": 0.0}
    required_scores = {"theme_id", "RLS", "RARS", "AVCR"}
    missing = required_scores - set(theme_scores.columns)
    if missing:
        raise ValueError(f"MISSING_THEME_SCORE_COLUMNS:{sorted(missing)}")

    themes = theme_scores.copy()
    for field in ("RLS", "RARS", "AVCR"):
        themes[field] = pd.to_numeric(themes[field], errors="coerce")
    enriched = mapping.merge(themes[["theme_id", "RLS", "RARS", "AVCR"]], on="theme_id", how="left")
    rows: list[dict[str, Any]] = []
    for (universe, isin), group in enriched.groupby(["universe", "isin"], dropna=False):
        exposure = pd.to_numeric(group["exposure_pct"], errors="coerce")
        confidence = pd.to_numeric(group["confidence_pct"], errors="coerce").fillna(50.0) / 100.0
        effective_weight = exposure.fillna(0.0) * confidence
        valid = effective_weight.gt(0) & group["RLS"].notna()
        if not valid.any():
            continue
        weights = effective_weight.loc[valid]
        weight_sum = float(weights.sum())
        rls = float(np.average(group.loc[valid, "RLS"].astype(float), weights=weights))
        rars = float(np.average(group.loc[valid, "RARS"].astype(float), weights=weights))
        avcr = float(np.average(group.loc[valid, "AVCR"].astype(float), weights=weights))
        strong = group.loc[valid & group["RLS"].ge(70.0), "theme_id"].astype(str).tolist()
        dangerous = group.loc[valid & group["AVCR"].ge(65.0), "theme_id"].astype(str).tolist()
        confluence = min(100.0, 50.0 + 12.5 * max(0, len(set(strong)) - 1)) if strong else 50.0
        rows.append(
            {
                "universe": str(universe),
                "isin": str(isin),
                "theme_rotation_exposure_score": round(rls, 4),
                "theme_risk_adjusted_score": round(rars, 4),
                "theme_weighted_AVCR": round(avcr, 4),
                "theme_confluence_score": round(confluence, 4),
                "mapped_theme_count": int(group.loc[valid, "theme_id"].nunique()),
                "strong_theme_count": int(len(set(strong))),
                "strong_themes": sorted(set(strong)),
                "overvalued_themes": sorted(set(dangerous)),
                "effective_mapping_weight": round(weight_sum, 4),
                "as_of": as_of,
                "decision_influence": 0.0,
            }
        )
    result = pd.DataFrame(rows)
    summary = {
        "status": "OK" if not result.empty else "NO_SCORABLE_MAPPING",
        "mapped_instruments": int(len(result)),
        "mapped_actions": int(result["universe"].eq("ACTION").sum()) if not result.empty else 0,
        "mapped_etfs": int(result["universe"].eq("ETF").sum()) if not result.empty else 0,
        "multi_theme_instruments": int(result["mapped_theme_count"].gt(1).sum()) if not result.empty else 0,
        "decision_influence": 0.0,
    }
    return result, summary


def build_mapping_worklist(
    instruments: pd.DataFrame,
    mapping: pd.DataFrame,
    *,
    universe: str,
    covered_isins: set[str] | None = None,
) -> pd.DataFrame:
    """List canonical instruments with no governed manual mapping or direct classification."""
    if "isin" not in instruments.columns:
        return pd.DataFrame(columns=["universe", "isin", "name", "sector", "status"])
    mapped = set(mapping.loc[mapping["universe"].astype(str).eq(universe), "isin"].astype(str)) if not mapping.empty else set()
    mapped |= {str(value) for value in (covered_isins or set()) if str(value)}
    pending = instruments.loc[~instruments["isin"].astype(str).isin(mapped)].copy()
    sector_column = next((name for name in ("sector_yf", "sector", "sector_bucket", "industry_yf") if name in pending.columns), None)
    name_column = "name" if "name" in pending.columns else None
    return pd.DataFrame(
        {
            "universe": universe,
            "isin": pending["isin"].astype(str),
            "name": pending[name_column].astype(str) if name_column else "",
            "sector": pending[sector_column].astype(str) if sector_column else "",
            "status": "MAPPING_REQUIRED",
        }
    )
