"""Governance validator for the PRE-2023 historical PEA symbol registry.

The registry is a data-governance input, never a model feature.  It must prove
both historical instrument existence and historical PEA-investability windows.
Current-snapshot backfills are rejected.  No 2023+ date can enter the governed
development universe.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd

PRE2023_START = pd.Timestamp("2010-01-01", tz="UTC")
PRE2023_END = pd.Timestamp("2022-12-31", tz="UTC")
HOLDOUT_START = pd.Timestamp("2023-01-01", tz="UTC")

REQUIRED_COLUMNS = [
    "instrument_id",
    "ticker",
    "eodhd_symbol",
    "isin",
    "exchange",
    "listing_start",
    "listing_end",
    "eligibility_start",
    "eligibility_end",
    "status_2022_12_31",
    "universe_method",
    "source_provider",
    "source_retrieved_at",
    "source_evidence",
    "eligibility_evidence",
]
ALLOWED_STATUS = {"active", "delisted", "merged", "renamed"}
ALLOWED_UNIVERSE_METHODS = {
    "provider_active_plus_delisted",
    "historical_membership_archive",
    "historical_regulatory_archive",
}
FORBIDDEN_UNIVERSE_METHODS = {
    "current_snapshot_backfill",
    "current_universe_backfill",
}


def _utc_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series.replace("", pd.NA), errors="coerce", utc=True)


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

    mandatory_nonblank = [
        "instrument_id", "ticker", "eodhd_symbol", "exchange",
        "status_2022_12_31", "universe_method", "source_provider",
        "source_retrieved_at", "source_evidence", "eligibility_evidence",
    ]
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

    methods = set(df["universe_method"].str.lower())
    if methods & FORBIDDEN_UNIVERSE_METHODS:
        raise ValueError("BLOCK_PRE2023_SURVIVORSHIP: current snapshot backfill is forbidden")
    bad_methods = methods - ALLOWED_UNIVERSE_METHODS
    if bad_methods:
        raise ValueError(f"BLOCK_PRE2023_UNIVERSE_METHOD: unsupported values {sorted(bad_methods)}")

    # Retrieval timestamp documents provenance only; it is never exposed as a
    # feature. It must parse so the corpus can be reproduced/audited.
    retrieved = pd.to_datetime(df["source_retrieved_at"], errors="coerce", utc=True)
    if retrieved.isna().any():
        raise ValueError("BLOCK_PRE2023_SOURCE_PROVENANCE: invalid source_retrieved_at")

    starts = _utc_series(df["listing_start"])
    ends = _utc_series(df["listing_end"])
    elig_starts = _utc_series(df["eligibility_start"])
    elig_ends = _utc_series(df["eligibility_end"])
    if starts.isna().any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_DATES: listing_start required/valid")
    if elig_starts.isna().any():
        raise ValueError("BLOCK_PRE2023_ELIGIBILITY: eligibility_start required/valid")
    if (ends.notna() & (ends < starts)).any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_DATES: listing_end before listing_start")
    if (elig_ends.notna() & (elig_ends < elig_starts)).any():
        raise ValueError("BLOCK_PRE2023_ELIGIBILITY: eligibility_end before eligibility_start")

    # Certified PRE2023 rows may never encode a boundary in the final holdout.
    for label, values in {
        "listing_start": starts,
        "listing_end": ends,
        "eligibility_start": elig_starts,
        "eligibility_end": elig_ends,
    }.items():
        if (values.dropna() >= HOLDOUT_START).any():
            raise ValueError(f"BLOCK_PRE2023_SYMBOLS_HOLDOUT: {label} reaches holdout")

    # Eligibility cannot precede listing or extend beyond a known listing end.
    if (elig_starts < starts).any():
        raise ValueError("BLOCK_PRE2023_ELIGIBILITY: eligibility starts before listing")
    if (ends.notna() & elig_ends.notna() & (elig_ends > ends)).any():
        raise ValueError("BLOCK_PRE2023_ELIGIBILITY: eligibility extends past listing end")

    # A non-active status must have an effective end date in the development
    # period; active at 2022-12-31 must not be given a synthetic listing end.
    status = df["status_2022_12_31"].str.lower()
    if ((status == "active") & ends.notna()).any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_STATUS: active row has listing_end")
    if ((status != "active") & ends.isna()).any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_STATUS: inactive row missing listing_end")

    overlaps_target = (starts <= PRE2023_END) & (ends.isna() | (ends >= PRE2023_START))
    eligible_target = (elig_starts <= PRE2023_END) & (elig_ends.isna() | (elig_ends >= PRE2023_START))
    if not (overlaps_target & eligible_target).any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_COVERAGE: no eligible instrument overlaps 2010-2022")

    # A provider-derived registry must contain historical exits; otherwise it
    # is observationally indistinguishable from a survivor-only snapshot.
    if set(status) <= {"active"}:
        raise ValueError("BLOCK_PRE2023_SURVIVORSHIP: registry contains only active survivors")

    out = df.copy()
    out["listing_start"] = starts
    out["listing_end"] = ends
    out["eligibility_start"] = elig_starts
    out["eligibility_end"] = elig_ends
    out["source_retrieved_at"] = retrieved
    out["status_2022_12_31"] = status
    out["universe_method"] = out["universe_method"].str.lower()
    return out


def export_collector_mapping(registry: pd.DataFrame, output: str | Path) -> Path:
    """Export stable instrument IDs as collector keys.

    The collector's historical key is intentionally NOT the display ticker:
    tickers can be reused or renamed.  `instrument_id` is stable and therefore
    prevents two distinct historical securities from being merged.
    """
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    mapping = registry[["instrument_id", "eodhd_symbol"]].drop_duplicates().copy()
    mapping = mapping.rename(columns={"instrument_id": "ticker"})
    mapping = mapping.sort_values(["ticker", "eodhd_symbol"])
    if mapping.empty:
        raise ValueError("BLOCK_PRE2023_SYMBOLS_COVERAGE: empty collector mapping")
    if mapping["ticker"].duplicated().any() or mapping["eodhd_symbol"].duplicated().any():
        raise ValueError("BLOCK_PRE2023_SYMBOLS_QUALITY: non-unique stable collector mapping")
    mapping.to_csv(out, index=False)
    return out
