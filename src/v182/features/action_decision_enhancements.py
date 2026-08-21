from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd


TEXT_RATING_SCORES = {
    "high": 100.0,
    "above average": 80.0,
    "average": 55.0,
    "below average": 25.0,
    "low": 0.0,
}


def _num(value: Any) -> float | None:
    try:
        x = float(str(value).replace(",", ".").replace("%", ""))
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _first_num(row: dict[str, Any], *fields: str) -> float | None:
    for field in fields:
        value = _num(row.get(field))
        if value is not None:
            return value
    return None


def _morningstar_score(rating: Any) -> float | None:
    x = _num(rating)
    if x is None or x < 1 or x > 5:
        return None
    return {1: 0.0, 2: 25.0, 3: 55.0, 4: 80.0, 5: 100.0}.get(int(round(x)))


def _morningstar_text_score(value: Any) -> float | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().lower().replace("_", " ").split())
    return TEXT_RATING_SCORES.get(text)


def _threshold_gt4_score(value: Any) -> float | None:
    """Explicit >4% reinforcement: below threshold is tied at zero."""
    x = _num(value)
    if x is None:
        return None
    if x < 4.0:
        return 0.0
    if x < 8.0:
        return 60.0 + (x - 4.0) * 7.5
    return min(100.0, 90.0 + (x - 8.0) * 2.5)


def _target_growth_score(upside_pct: Any) -> float | None:
    upside = _num(upside_pct)
    if upside is None:
        return None
    if upside <= 0:
        return max(0.0, 20.0 + upside)
    if upside < 10:
        return 20.0 + 3.0 * upside
    if upside < 20:
        return 50.0 + 2.5 * (upside - 10.0)
    return min(100.0, 75.0 + 1.25 * (upside - 20.0))


def _target_revision_confirmed(row: dict[str, Any], target_raw: float | None) -> bool | None:
    if target_raw is None or target_raw < 4.0:
        return None
    delta = _first_num(row, "consensus_delta_4w", "consensus_delta_4w_v21")
    upgrades = _first_num(row, "net_upgrades_30d_v21", "net_upgrades_30d")
    if delta is None and upgrades is None:
        return None
    return bool((delta is not None and delta > 0) or (upgrades is not None and upgrades > 0))


def build_action_enhancement_observations(actions: pd.DataFrame) -> list[dict]:
    """Build observed-only Action features with no neutral imputation.

    Textual Morningstar fields and target-revision coherence are emitted as
    diagnostics. They do not replace the governed numeric Morningstar criterion.
    """
    now = datetime.now(timezone.utc).isoformat()
    output: list[dict] = []
    for row in actions.to_dict("records"):
        isin = str(row.get("isin", "") or "")
        numeric_rating = _first_num(row, "morningstar_rating")
        morning = _morningstar_score(numeric_rating)
        text_rating = None
        for field in ("morningstar_rating_text", "morningstar_qualitative_rating"):
            text_rating = _morningstar_text_score(row.get(field))
            if text_rating is not None:
                break

        dividend_raw = _first_num(row, "dividend_yield_pct", "dividend_yield_v21_pct")
        target_raw = _first_num(row, "upside_pct_yf", "upside_pct", "target_upside_pct_v21")
        dividend = _threshold_gt4_score(dividend_raw)
        target_gt4 = _threshold_gt4_score(target_raw)
        target_shape = _target_growth_score(target_raw)
        target_revision_confirmed = _target_revision_confirmed(row, target_raw)

        total_parts: list[float] = []
        total_weights: list[float] = []
        if target_shape is not None:
            total_parts.append(target_shape * 0.75)
            total_weights.append(0.75)
        if dividend is not None:
            total_parts.append(dividend * 0.25)
            total_weights.append(0.25)
        total = sum(total_parts) / sum(total_weights) if total_weights else None

        values: dict[str, Any] = {
            "morningstar_action_score": morning,
            "morningstar_text_rating_score": text_rating,
            "dividend_gt4_score": dividend,
            "target_upside_gt4_score": target_gt4,
            "target_upside_growth_score": target_shape,
            "target_revision_confirmed": target_revision_confirmed,
            "total_return_potential_score": total,
        }
        for field, value in values.items():
            if value is None:
                continue
            if field == "morningstar_action_score":
                source = "DERIVED_MORNINGSTAR_STOCK_RATING"
                evidence = "B"
            elif field == "morningstar_text_rating_score":
                source = "DERIVED_MORNINGSTAR_TEXT_RATING_DIAGNOSTIC"
                evidence = "C"
            elif field == "target_revision_confirmed":
                source = "DERIVED_TARGET_REVISION_CONSISTENCY_DIAGNOSTIC"
                evidence = "C"
            else:
                source = "DERIVED_TARGET_DIVIDEND_OBSERVED"
                evidence = "C"
            normalized = bool(value) if isinstance(value, (bool, np.bool_)) else round(float(value), 4)
            output.append(
                {
                    "universe": "ACTION",
                    "isin": isin,
                    "field": field,
                    "value": normalized,
                    "source": source,
                    "collected_at": now,
                    "as_of": now[:10],
                    "evidence_level": evidence,
                    "validation_status": "AUTO_MATCH",
                }
            )
    return output
