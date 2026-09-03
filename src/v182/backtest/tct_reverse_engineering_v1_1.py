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


def build_catalyst_event_features_safe(
    base: pd.DataFrame,
    events: pd.DataFrame,
    *,
    id_col: str = "instrument_id",
    date_col: str = "date",
    event_time_col: str = "observed_at_utc",
    event_type_col: str = "event_type",
    windows_days: tuple[int, ...] | list[int] = (1, 3, 5, 10, 20),
) -> tuple[pd.DataFrame, list[str]]:
    """Encode only already-observed catalyst events using explicit ns timestamps.

    Pandas 3 may preserve microsecond-resolution datetimes. This function forces
    both signal dates and event timestamps to datetime64[ns, UTC] before integer
    search arithmetic, preventing 1000x window distortion.
    """
    out = base.copy().reset_index(drop=True)
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce", utc=True).astype("datetime64[ns, UTC]")
    hist = events.copy()
    required = {id_col, event_time_col, event_type_col}
    if not required.issubset(hist.columns):
        raise ValueError("CATALYST_FIELDS_MISSING:" + ",".join(sorted(required - set(hist.columns))))
    hist[event_time_col] = pd.to_datetime(hist[event_time_col], errors="coerce", utc=True).astype("datetime64[ns, UTC]")
    if hist[event_time_col].isna().any():
        raise ValueError("PIT_TIMESTAMP_INVALID")
    hist[event_type_col] = hist[event_type_col].astype(str).str.upper().str.strip()
    types = sorted(t for t in hist[event_type_col].unique() if t)
    feature_cols: list[str] = []
    date_ns = out[date_col].astype("int64").to_numpy()
    day_ns = pd.Timedelta(days=1).value

    for event_type in types:
        safe = "".join(ch if ch.isalnum() else "_" for ch in event_type.lower()).strip("_")
        typed = hist[hist[event_type_col] == event_type]
        for window in windows_days:
            col = f"catalyst_{safe}_count_{int(window)}d"
            out[col] = 0
            feature_cols.append(col)
        age_col = f"catalyst_{safe}_age_days"
        out[age_col] = np.nan
        feature_cols.append(age_col)

        for instrument, idx in out.groupby(id_col, sort=False).groups.items():
            event_times = (
                typed.loc[typed[id_col] == instrument, event_time_col]
                .sort_values()
                .astype("int64")
                .to_numpy()
            )
            if len(event_times) == 0:
                continue
            positions = np.asarray(list(idx), dtype=int)
            signals = date_ns[positions]
            right = np.searchsorted(event_times, signals, side="right")
            for window in windows_days:
                left_boundary = signals - int(window) * day_ns
                left = np.searchsorted(event_times, left_boundary, side="right")
                out.loc[positions, f"catalyst_{safe}_count_{int(window)}d"] = right - left
            has_prior = right > 0
            prior_time = np.full(len(signals), np.nan)
            prior_time[has_prior] = event_times[right[has_prior] - 1]
            out.loc[positions, age_col] = (signals - prior_time) / day_ns
    return out, feature_cols


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
