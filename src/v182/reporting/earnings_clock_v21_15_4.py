from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd


VERSION = "EARNINGS_CLOCK_V21_15_4"
TIMESTAMP_FIELD = "next_earnings_timestamp_yf"
DAYS_FIELD = "days_to_earnings"
FLAG_7D = "earnings_within_7d_flag"
FLAG_30D = "earnings_within_30d_flag"


def refresh_frame(frame: pd.DataFrame, *, now: datetime | None = None) -> tuple[pd.DataFrame, dict]:
    """Recompute time-derived earnings fields from retained source timestamp.

    Yahoo's next earnings timestamp is source evidence; the relative day count and
    7/30-day flags are deterministic clock-derived fields. Recomputing them locally
    prevents a valid source cache from freezing yesterday's proximity state and
    avoids a network refresh solely because the wall clock moved.
    """
    out = frame.copy()
    if out.empty or TIMESTAMP_FIELD not in out.columns:
        return out, {
            "version": VERSION,
            "rows": int(len(out)),
            "timestamps_available": 0,
            "rows_recomputed": 0,
            "source_timestamp_changed": False,
        }

    anchor = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    epoch = pd.to_numeric(out[TIMESTAMP_FIELD], errors="coerce")
    valid = epoch.notna() & np.isfinite(epoch) & epoch.gt(0)
    if not bool(valid.any()):
        return out, {
            "version": VERSION,
            "rows": int(len(out)),
            "timestamps_available": 0,
            "rows_recomputed": 0,
            "source_timestamp_changed": False,
        }

    days = (epoch - anchor.timestamp()) / 86400.0
    for field in (DAYS_FIELD, FLAG_7D, FLAG_30D):
        if field not in out.columns:
            out[field] = np.nan

    # Keep these deterministic clock-derived columns numeric. Pandas 3 rejects
    # object arrays assigned into existing float blocks even when every element is
    # numerically representable. Numeric assignment is lossless and preserves the
    # exact historical semantics of these three fields.
    out.loc[valid, DAYS_FIELD] = days.loc[valid].round(3)
    out.loc[valid, FLAG_7D] = days.loc[valid].between(0.0, 7.0).astype(float)
    out.loc[valid, FLAG_30D] = days.loc[valid].between(0.0, 30.0).astype(float)

    return out, {
        "version": VERSION,
        "rows": int(len(out)),
        "timestamps_available": int(valid.sum()),
        "rows_recomputed": int(valid.sum()),
        "past_event_timestamps": int((valid & days.lt(0)).sum()),
        "within_7d": int((valid & days.between(0.0, 7.0)).sum()),
        "within_30d": int((valid & days.between(0.0, 30.0)).sum()),
        "source_timestamp_changed": False,
        "network_calls": 0,
        "decision_logic_changed": False,
    }
