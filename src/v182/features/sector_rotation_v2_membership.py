from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from v182.features.sector_rotation_v2 import _sector_series


IDENTITY_COLUMNS = ("isin", "yahoo_ticker", "name")
INVALID_TEXT = {"", "nan", "none", "n/a", "na", "unknown", "<na>"}


def _clean_text(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    valid = ~text.str.casefold().fillna("").isin(INVALID_TEXT)
    return text.where(valid)


def build_membership_snapshot(
    actions: pd.DataFrame,
    scored_sectors: Iterable[str],
    *,
    as_of: str,
    model_version: str,
) -> pd.DataFrame:
    """Freeze sector membership used by V2 so later OOS outcomes avoid survivorship drift."""
    work = actions.copy()
    work["sector"] = _sector_series(work)
    scored = {str(value).strip() for value in scored_sectors if str(value).strip()}
    work = work.loc[work["sector"].isin(scored)].copy()

    for column in IDENTITY_COLUMNS:
        if column not in work.columns:
            work[column] = pd.NA
        work[column] = _clean_text(work[column])

    key = work["isin"].copy()
    key = key.fillna(work["yahoo_ticker"])
    key = key.fillna(work["name"])
    work["instrument_key"] = key
    work = work.dropna(subset=["instrument_key"])

    out = work[["sector", "instrument_key", *IDENTITY_COLUMNS]].copy()
    out.insert(0, "model_version", str(model_version))
    out.insert(0, "as_of", str(as_of))
    return out.drop_duplicates(["as_of", "model_version", "sector", "instrument_key"]).reset_index(drop=True)


def append_membership_history(snapshot: pd.DataFrame, path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        old = pd.read_csv(output, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False)
        combined = pd.concat([old, snapshot], ignore_index=True, sort=False)
    else:
        combined = snapshot.copy()
    keys = ["as_of", "model_version", "sector", "instrument_key"]
    missing = [key for key in keys if key not in combined.columns]
    if missing:
        raise ValueError(f"MISSING_MEMBERSHIP_KEYS:{missing}")
    combined = combined.drop_duplicates(keys, keep="last")
    combined.to_csv(output, sep=";", index=False, encoding="utf-8-sig")
