from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from v182.backtest.tct_reverse_engineering_v1 import (
    ReverseEngineeringConfig,
    asof_snapshot_features,
    build_forward_labels,
    build_technical_features,
    sanitize_feature_columns,
)


def _resolve_date_col(frame: pd.DataFrame, preferred: str = "date") -> str:
    candidates = (preferred, "date", "as_of_date", "session_date")
    lower = {str(c).lower(): str(c) for c in frame.columns}
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    raise ValueError("SPLIT_DATE_COLUMN_MISSING")


def infer_research_boundaries(frame: pd.DataFrame, date_col: str = "date") -> dict[str, pd.Timestamp]:
    """Infer conservative chronological research blocks from the history that exists.

    Preferred policy for the current governed cache (2020+):
    Discovery 2020-2021, Development 2022-2023, Validation 2024, Holdout 2025+.
    For deeper histories, use the historical protocol ending with the same 2025+ holdout.
    """
    resolved_date_col = _resolve_date_col(frame, date_col)
    dt = pd.to_datetime(frame[resolved_date_col], errors="coerce")
    start = dt.min()
    end = dt.max()
    if pd.isna(start) or pd.isna(end):
        raise ValueError("SPLIT_DATE_RANGE_INVALID")

    if start <= pd.Timestamp("2018-12-31"):
        return {
            "discovery_start": start.normalize(),
            "development_start": pd.Timestamp("2019-01-01"),
            "validation_start": pd.Timestamp("2023-01-01"),
            "holdout_start": pd.Timestamp("2025-01-01"),
            "end": end.normalize(),
        }
    if start <= pd.Timestamp("2020-12-31"):
        return {
            "discovery_start": start.normalize(),
            "development_start": pd.Timestamp("2022-01-01"),
            "validation_start": pd.Timestamp("2024-01-01"),
            "holdout_start": pd.Timestamp("2025-01-01"),
            "end": end.normalize(),
        }

    years = sorted(int(y) for y in dt.dropna().dt.year.unique())
    if len(years) < 4:
        raise ValueError("INSUFFICIENT_HISTORY_FOR_4_BLOCK_SPLIT")
    return {
        "discovery_start": pd.Timestamp(f"{years[0]}-01-01"),
        "development_start": pd.Timestamp(f"{years[max(1, len(years)//3)]}-01-01"),
        "validation_start": pd.Timestamp(f"{years[max(2, (2*len(years))//3)]}-01-01"),
        "holdout_start": pd.Timestamp(f"{years[-1]}-01-01"),
        "end": end.normalize(),
    }


def chronological_split_adaptive(
    frame: pd.DataFrame,
    date_col: str = "date",
    *,
    purge_sessions: int = 20,
) -> pd.DataFrame:
    out = frame.copy()
    resolved_date_col = _resolve_date_col(out, date_col)
    dt = pd.to_datetime(out[resolved_date_col], errors="coerce")
    bounds = infer_research_boundaries(out, date_col=resolved_date_col)
    dev = bounds["development_start"]
    val = bounds["validation_start"]
    hold = bounds["holdout_start"]

    out["research_split"] = np.select(
        [dt < dev, (dt >= dev) & (dt < val), (dt >= val) & (dt < hold), dt >= hold],
        ["DISCOVERY", "DEVELOPMENT", "VALIDATION", "HOLDOUT"],
        default="OUT_OF_SCOPE",
    )
    out["research_split_protocol"] = (
        f"D<{dev.date()}|DEV<{val.date()}|VAL<{hold.date()}|HOLDOUT>={hold.date()}"
    )

    if purge_sessions > 0:
        for boundary in (dev, val, hold):
            lower = boundary - pd.offsets.BDay(int(purge_sessions))
            crossing = (dt < boundary) & (dt >= lower)
            out.loc[crossing, "research_split"] = "PURGED"
    return out


def prepare_research_matrix_adaptive(
    ohlcv: pd.DataFrame,
    *,
    exogenous_history: pd.DataFrame | None = None,
    exogenous_value_cols: tuple[str, ...] | list[str] | None = None,
    cfg: ReverseEngineeringConfig = ReverseEngineeringConfig(),
) -> tuple[pd.DataFrame, list[str]]:
    technical = build_technical_features(ohlcv)
    labels = build_forward_labels(ohlcv, cfg)
    label_cols = [c for c in labels.columns if c.startswith(("fwd_", "label_", "first_hit_", "entry_price_"))]
    base = technical.merge(
        labels[["instrument_id", "date", *label_cols]],
        on=["instrument_id", "date"],
        how="left",
        validate="one_to_one",
    )
    feature_cols = [
        c for c in technical.columns
        if c not in {"instrument_id", "date", "open", "high", "low", "close", "volume"}
    ]
    if exogenous_history is not None and not exogenous_history.empty:
        before = set(base.columns)
        base = asof_snapshot_features(
            base,
            exogenous_history,
            value_cols=exogenous_value_cols,
            delta_windows=cfg.delta_windows,
        )
        feature_cols.extend([c for c in base.columns if c not in before and c != "observed_at_utc"])
    base = chronological_split_adaptive(base, purge_sessions=max(cfg.horizons))
    feature_cols = sanitize_feature_columns(feature_cols)
    return base, feature_cols


def effective_config_for_history(
    frame: pd.DataFrame,
    cfg: ReverseEngineeringConfig = ReverseEngineeringConfig(),
) -> ReverseEngineeringConfig:
    """Bind holdout metadata to the actual adaptive split without changing alpha rules."""
    bounds = infer_research_boundaries(frame)
    return replace(cfg, holdout_start=str(bounds["holdout_start"].date()), holdout_end=str(bounds["end"].date()))
