from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def load_macro_sensitivity(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    if "theme_id" not in frame.columns:
        raise ValueError("MISSING_THEME_ID")
    return frame


def compute_macro_sector_scores(
    macro_factors: dict[str, float],
    sensitivity: pd.DataFrame,
    *,
    minimum_factor_coverage: float = 0.50,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Translate normalized PIT macro factors into sector/theme compatibility scores.

    Each macro factor is expected on a 0-100 scale where 50 is neutral and a
    higher value means more of the named factor (e.g. stronger growth, higher
    inflation, higher real rates). Sensitivities are research priors in [-1, 1].
    The result remains shadow-only until the priors are PIT/OOS validated.
    """
    if not macro_factors:
        return pd.DataFrame(), {"status": "NO_MACRO_FACTORS", "decision_influence": 0.0}
    available = {key: float(value) for key, value in macro_factors.items() if np.isfinite(float(value))}
    if not available:
        return pd.DataFrame(), {"status": "NO_VALID_MACRO_FACTORS", "decision_influence": 0.0}

    factor_columns = [column for column in sensitivity.columns if column not in {"theme_id", "status"}]
    usable = [factor for factor in factor_columns if factor in available]
    coverage = len(usable) / len(factor_columns) if factor_columns else 0.0
    rows: list[dict[str, Any]] = []
    for _, row in sensitivity.iterrows():
        weighted = 0.0
        denom = 0.0
        contributions: dict[str, float] = {}
        for factor in usable:
            sensitivity_value = pd.to_numeric(pd.Series([row.get(factor)]), errors="coerce").iloc[0]
            if pd.isna(sensitivity_value):
                continue
            sensitivity_value = float(np.clip(float(sensitivity_value), -1.0, 1.0))
            centered = float(available[factor]) - 50.0
            contribution = sensitivity_value * centered
            weighted += contribution
            denom += abs(sensitivity_value)
            contributions[factor] = round(contribution, 4)
        score = 50.0 if denom == 0 else float(np.clip(50.0 + weighted / denom, 0.0, 100.0))
        effective_score = 50.0 + coverage * (score - 50.0)
        rows.append(
            {
                "theme_id": str(row["theme_id"]),
                "sector_macro_score": round(float(np.clip(effective_score, 0.0, 100.0)), 4),
                "factor_coverage_pct": round(coverage * 100.0, 4),
                "macro_evidence_sufficient": bool(coverage >= minimum_factor_coverage),
                "contributions": contributions,
                "status": row.get("status", "RESEARCH_PRIOR"),
                "decision_influence": 0.0,
            }
        )
    result = pd.DataFrame(rows)
    summary = {
        "status": "OK",
        "factor_coverage_pct": round(coverage * 100.0, 4),
        "usable_factors": usable,
        "theme_count": int(len(result)),
        "minimum_factor_coverage": float(minimum_factor_coverage),
        "decision_influence": 0.0,
    }
    return result, summary
