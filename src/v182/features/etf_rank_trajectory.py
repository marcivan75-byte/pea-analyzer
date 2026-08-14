from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import math
import pandas as pd

RANK_FIELDS = ("rank_cat_1y", "rank_cat_3y", "rank_cat_5y")
TRAJECTORY_DEFINITIONS = {
    "rank_cat_trajectory_12m": ("rank_cat_1y", 365),
    "rank_cat_trajectory_24m": ("rank_cat_3y", 730),
    "rank_cat_trajectory_36m": ("rank_cat_5y", 1095),
}


def _rank(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not (1.0 <= parsed <= 100.0):
        return None
    return parsed


def _read_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["isin", "observed_at", *RANK_FIELDS])
    try:
        frame = pd.read_csv(path, dtype=str)
    except (OSError, pd.errors.ParserError, UnicodeError):
        return pd.DataFrame(columns=["isin", "observed_at", *RANK_FIELDS])
    return frame


def _closest_prior(prior: pd.DataFrame, target: pd.Timestamp, field: str, tolerance_days: int) -> float | None:
    if prior.empty or field not in prior.columns or "observed_at" not in prior.columns:
        return None
    work = prior.copy()
    work["_ts"] = pd.to_datetime(work["observed_at"], utc=True, errors="coerce")
    work["_rank"] = pd.to_numeric(work[field], errors="coerce")
    work = work.dropna(subset=["_ts", "_rank"])
    work = work[work["_rank"].between(1.0, 100.0)]
    if work.empty:
        return None
    distance = (work["_ts"] - target).abs().dt.total_seconds() / 86400.0
    idx = distance.idxmin()
    if float(distance.loc[idx]) > float(tolerance_days):
        return None
    return float(work.loc[idx, "_rank"])


def update_etf_rank_trajectories(
    etfs: pd.DataFrame,
    history_path: str | Path,
    *,
    observed_at: datetime | None = None,
    tolerance_days: int = 62,
) -> tuple[list[dict], list[dict]]:
    """Persist canonical rank_cat snapshots and emit 12/24/36m PIT trajectory fields.

    Canonical rank_cat values follow the audit semantic 1=best, 100=worst.
    A positive trajectory means improvement because prior rank - current rank > 0.
    First snapshots or missing lookback windows remain missing; no neutral value is
    fabricated. Boursorama raw annual ranks are intentionally not substituted for
    these canonical percentile ranks unless their denominator/semantics are proven.
    """
    now = observed_at or datetime.now(timezone.utc)
    now_ts = pd.Timestamp(now)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")
    else:
        now_ts = now_ts.tz_convert("UTC")
    history_path = Path(history_path)
    history = _read_history(history_path)
    observations: list[dict] = []
    failures: list[dict] = []
    snapshot_rows: list[dict] = []

    if "isin" not in etfs.columns:
        return [], [{"source":"ETF rank trajectory","reason":"ISIN_COLUMN_MISSING"}]

    for _, row in etfs.drop_duplicates("isin").iterrows():
        isin = str(row.get("isin", "") or "").strip()
        if not isin:
            continue
        current = {field: _rank(row.get(field)) for field in RANK_FIELDS}
        if not any(value is not None for value in current.values()):
            continue
        prior = history.loc[history.get("isin", pd.Series(dtype=str)).astype(str) == isin].copy() if not history.empty and "isin" in history.columns else pd.DataFrame()
        snapshot_rows.append({"isin": isin, "observed_at": now_ts.isoformat(), **current})
        for output_field, (source_field, days) in TRAJECTORY_DEFINITIONS.items():
            current_rank = current.get(source_field)
            if current_rank is None:
                continue
            prior_rank = _closest_prior(prior, now_ts - pd.Timedelta(days=days), source_field, tolerance_days)
            if prior_rank is None:
                continue
            change = prior_rank - current_rank
            observations.append({
                "universe":"ETF",
                "isin":isin,
                "field":output_field,
                "value":round(change,4),
                "source":"ETF canonical category-rank PIT snapshots",
                "collected_at":now_ts.isoformat(),
                "as_of":now_ts.date().isoformat(),
                "evidence_level":"B",
                "validation_status":"AUTO_MATCH",
            })

    if snapshot_rows:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        updated = pd.concat([history, pd.DataFrame(snapshot_rows)], ignore_index=True, sort=False)
        updated["_observed_date"] = pd.to_datetime(updated["observed_at"], utc=True, errors="coerce").dt.date.astype(str)
        updated = updated.drop_duplicates(subset=["isin", "_observed_date"], keep="last").drop(columns=["_observed_date"])
        updated = updated.sort_values(["isin", "observed_at"])
        updated.to_csv(history_path, index=False, encoding="utf-8-sig")
    return observations, failures
