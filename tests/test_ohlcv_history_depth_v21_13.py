from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.audit.ohlcv_history_depth import (
    _instrument_row,
    load_cache_series,
)


def _business_monthly_series(start: str, end: str) -> pd.Series:
    index = pd.date_range(start, end, freq="B", tz="UTC")
    return pd.Series(range(1, len(index) + 1), index=index, dtype=float)


def test_full_history_covers_primary_and_stress():
    close = _business_monthly_series("2020-01-02", "2026-08-20")
    row = _instrument_row(
        "ACTION",
        "FR0000000001",
        "AAA.PA",
        close,
        primary_start=pd.Timestamp("2023-01-01", tz="UTC"),
        as_of=pd.Timestamp("2026-08-20", tz="UTC"),
    )
    assert row["primary_status"] == "PRIMARY_FULL_FROM_ANCHOR"
    assert row["expected_primary_months"] == 44
    assert row["observed_primary_months"] == 44
    assert row["stress_status"] == "STRESS_FULL_2020_2022"
    assert row["observed_stress_months"] == 36


def test_short_post_anchor_history_is_unresolved_not_assumed_new_listing():
    close = _business_monthly_series("2025-04-01", "2026-08-20")
    row = _instrument_row(
        "ETF",
        "FR0000000002",
        "BBB.PA",
        close,
        primary_start=pd.Timestamp("2023-01-01", tz="UTC"),
        as_of=pd.Timestamp("2026-08-20", tz="UTC"),
    )
    assert row["primary_status"] == "START_AFTER_ANCHOR_UNRESOLVED"
    assert row["short_history_reason"] == "NEEDS_TRUSTED_LISTING_OR_INCEPTION_DATE"
    assert row["stress_status"] == "NO_STRESS_HISTORY"


def test_missing_whole_primary_month_is_detected():
    close = _business_monthly_series("2020-01-02", "2026-08-20")
    close = close.loc[~((close.index.year == 2024) & (close.index.month == 6))]
    row = _instrument_row(
        "ACTION",
        "FR0000000003",
        "CCC.PA",
        close,
        primary_start=pd.Timestamp("2023-01-01", tz="UTC"),
        as_of=pd.Timestamp("2026-08-20", tz="UTC"),
    )
    assert row["primary_status"] == "PRIMARY_MISSING_CALENDAR_MONTHS"
    assert "2024-06" in row["missing_primary_months"]


def test_cache_reader_ignores_union_index_padding(tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    columns = pd.MultiIndex.from_product([["AAA.PA", "BBB.PA"], ["Close"]])
    frame = pd.DataFrame(
        [[10.0, float("nan")], [11.0, 20.0], [12.0, 21.0]],
        index=pd.date_range("2026-08-18", periods=3),
        columns=columns,
    )
    frame.to_parquet(cache / "history_00000.parquet")
    series, failures = load_cache_series(cache)
    assert failures == []
    assert len(series["AAA.PA"]) == 3
    assert len(series["BBB.PA"]) == 2
    assert series["BBB.PA"].index.min() == pd.Timestamp("2026-08-19", tz="UTC")


def test_master_config_forces_new_ten_year_cache_generation():
    config = json.loads(Path("config/V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    yf = config["yfinance"]
    assert yf["history_period"] == "10y"
    assert yf["required_history_start"] == "2020-01-01"
    assert yf["cache_generation"] == "V21.13_10Y_STRESS_READY"
    assert yf["actions_batch_size"] == 96
    assert yf["etf_batch_size"] == 48
