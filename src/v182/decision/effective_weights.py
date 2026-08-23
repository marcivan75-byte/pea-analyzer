from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from v182.decision.committee_master import active_criteria, resolve_field


def _text_values(frame: pd.DataFrame, column: str) -> np.ndarray:
    """Match historical ``str(row.get(column, '') or '')`` semantics in row order."""
    if column not in frame.columns:
        return np.full(len(frame), "", dtype=object)
    return np.asarray([str(value or "") for value in frame[column].tolist()], dtype=object)


def effective_weight_report(
    frame: pd.DataFrame,
    registry: dict,
    asset_class: str,
    horizons: Iterable[str],
) -> pd.DataFrame:
    """Expose exact effective weights with vectorized row/criterion expansion.

    The historical implementation iterated every row and every active criterion
    in Python. This implementation keeps the same criterion resolution functions,
    horizon order, row order, criterion order, missing-data renormalization and
    six-decimal output rounding, while moving availability/weight arithmetic to
    NumPy. It is reporting-only and does not alter scoring or decisions.
    """
    n_rows = len(frame)
    if n_rows == 0:
        return pd.DataFrame()

    isin_values = _text_values(frame, "isin")
    name_values = _text_values(frame, "name")
    parts: list[pd.DataFrame] = []

    for horizon in horizons:
        active = active_criteria(registry, horizon)
        if not active:
            continue

        criterion_names: list[str] = []
        sources: list[str] = []
        weights: list[float] = []
        directions: list[str] = []
        availability_columns: list[np.ndarray] = []

        for name, weight, direction in active:
            values, source = resolve_field(frame, name)
            criterion_names.append(name)
            sources.append(source)
            weights.append(float(weight))
            directions.append(direction)
            if values is None:
                availability_columns.append(np.zeros(n_rows, dtype=bool))
            else:
                aligned = values.reindex(frame.index)
                availability_columns.append(aligned.notna().to_numpy(dtype=bool, copy=False))

        availability = np.column_stack(availability_columns)
        weight_array = np.asarray(weights, dtype=float)
        denom = availability.astype(float) @ weight_array
        weighted = availability.astype(float) * weight_array.reshape(1, -1)
        effective = np.divide(
            weighted * 100.0,
            denom.reshape(-1, 1),
            out=np.zeros_like(weighted, dtype=float),
            where=denom.reshape(-1, 1) > 0,
        )

        criteria_count = len(criterion_names)
        row_repeat = np.repeat(np.arange(n_rows), criteria_count)
        criterion_tile = np.tile(np.arange(criteria_count), n_rows)

        parts.append(
            pd.DataFrame(
                {
                    "asset_class": np.full(n_rows * criteria_count, asset_class, dtype=object),
                    "horizon": np.full(n_rows * criteria_count, horizon, dtype=object),
                    "isin": isin_values[row_repeat],
                    "name": name_values[row_repeat],
                    "criterion": np.asarray(criterion_names, dtype=object)[criterion_tile],
                    "raw_weight_pct": np.round(weight_array[criterion_tile] * 100.0, 6),
                    "criterion_available": availability.reshape(-1).astype(bool),
                    "effective_weight_pct": np.round(effective.reshape(-1), 6),
                    "available_raw_weight_pct": np.round(np.repeat(denom, criteria_count) * 100.0, 6),
                    "normalization_policy": np.full(
                        n_rows * criteria_count,
                        "AVAILABLE_CRITERIA_RENORMALIZED_TO_100",
                        dtype=object,
                    ),
                    "resolution": np.asarray(sources, dtype=object)[criterion_tile],
                }
            )
        )

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True, sort=False)
