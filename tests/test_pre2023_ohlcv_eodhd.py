from pathlib import Path

import pandas as pd
import pytest

from v182.data.pre2023_ohlcv_eodhd import HOLDOUT_START, _load_symbols, _validate_bars, _validate_window


def test_window_rejects_holdout_start():
    with pytest.raises(ValueError, match="HOLDOUT_LEAK"):
        _validate_window("2022-01-01", "2023-01-01")


def test_window_accepts_pre2023_only():
    start, end = _validate_window("2012-01-01", "2022-12-31")
    assert start < end < HOLDOUT_START


def test_symbol_mapping_is_unique(tmp_path: Path):
    p = tmp_path / "symbols.csv"
    p.write_text("ticker,eodhd_symbol\nAI.PA,AI.PA\nAI.PA,OR.PA\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        _load_symbols(p)


def test_bars_reject_holdout_leak():
    raw = pd.DataFrame([
        {"date":"2022-12-30","open":10,"high":11,"low":9,"close":10.5,"volume":100},
        {"date":"2023-01-02","open":10,"high":11,"low":9,"close":10.5,"volume":100},
    ])
    with pytest.raises(ValueError, match="HOLDOUT_LEAK"):
        _validate_bars(raw, "AI.PA", pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2022-12-31", tz="UTC"))


def test_bars_reject_impossible_geometry():
    raw = pd.DataFrame([
        {"date":"2022-12-30","open":10,"high":9,"low":8,"close":10.5,"volume":100},
    ])
    with pytest.raises(ValueError, match="geometry"):
        _validate_bars(raw, "AI.PA", pd.Timestamp("2022-01-01", tz="UTC"), pd.Timestamp("2022-12-31", tz="UTC"))
