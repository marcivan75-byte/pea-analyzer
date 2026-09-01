"""Governance validator for the PRE-2023 historical symbol registry.

This module does not invent or enrich market data. It validates a supplied
historical instrument registry before any 2010-2022 OHLCV corpus can be built.
The design is fail-closed and is intended to limit survivorship/ticker-history
bias while preserving strict separation from the 2023-2026 holdout.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd

PRE2023_START = pd.Timestamp("2010-01-01", tz="UTC")
HOLDOUT_START = pd.Timestamp("2023-01-01", tz="UTC")

REQUIRED_COLUMNS = [
    "instrument_id",
    "ticker",
    "eodhd_symbol",
    "isin",
    "exchange",
    "listing_start",
    "listing_end",
    "status_2022_12_31",
    "source_evidence",
]
ALLOWED_STATUS = {"active", "delisted", "merged", "renamed", "unknown"}


def _date_or_na(value: str) -> pd.Timestamp | pd.NaT:
    if value is None or str(value).strip() == "":
        return pd.NaT
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def validate_registry(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        raise ValueError(f"BLOCK_PRE2023_SYMBOLS: missing/empty registry {p}")
    df = pd.read_csv(p, dtype=str).fillna("")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"BLOCK_PRE2023_SYMBOLS_SCHEMA: missing columns {missing}")
    df = df[REQUIRED_COLUMNS].copy()
    for c in REQUIRED_COLUMNS:
        df[c] = df[c].str.strip()

    mandatory_nonblank = ["instrument_id", "ticker", "eodhd_symbol", "exchange", "status_2022_12_31", "source_evidence"]
    if any((df[c] == "").any() for c in mandatory_nonblank):
        raise ValueError("BLOCK_PRE2023_SYMBOLS_QUALITY: blank mandatory field")
    if df["instrument_id"].duplicated().any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_QUALITY: duplicate instrument_id")
    if df["eodhd_symbol"].duplicated().any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_QUALITY: duplicate eodhd_symbol")

    statuses = set(df["status_2022_12_31"].str.lower())
    bad_status = statuses - ALLOWED_STATUS
    if bad_status:
        raise ValueError(f"BLOCK_PRE2023_SYMBOLS_STATUS: unsupported values {sorted(bad_status)}")

    starts = df["listing_start"].map(_date_or_na)
    ends = df["listing_end"].map(_date_or_na)
    if starts.isna().any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_DATES: listing_start required")
    bad_order = ends.notna() & (ends < starts)
    if bad_order.any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_DATES: listing_end before listing_start")
    if (starts >= HOLDOUT_START).any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_HOLDOUT: registry contains instrument starting in holdout")
    if (ends.dropna() >= HOLDOUT_START).any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_HOLDOUT: listing_end reaches holdout")

    overlaps_target = (starts <= pd.Timestamp("2022-12-31", tz="UTC")) & (ends.isna() | (ends >= PRE2023_START))
    if not overlaps_target.any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_COVERAGE: no instrument overlaps 2010-2022")

    # A registry made only of names still active at the end of 2022 is not
    # sufficient evidence against survivorship bias. Fail closed unless at
    # least one non-active historical row exists. This gate may only be
    # overridden by replacing the registry with a historically complete one.
    if set(df["status_2022_12_31"].str.lower()) <= {"active"}:
        raise ValueError("BLOCK_PRE2023_SURVIVORSHIP: registry contains only active survivors")

    out = df.copy()
    out["listing_start"] = starts
    out["listing_end"] = ends
    out["status_2022_12_31"] = out["status_2022_12_31"].str.lower()
    return out


def export_collector_mapping(registry: pd.DataFrame, output: str | Path) -> Path:
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    mapping = registry[["ticker", "eodhd_symbol"]].drop_duplicates().sort_values(["ticker", "eodhd_symbol"])
    if mapping.empty:
        raise ValueError("BLOCK_PRE2023_SYMBOLS_COVERAGE: empty collector mapping")
    mapping.to_csv(out, index=False)
    return out
