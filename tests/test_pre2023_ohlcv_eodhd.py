from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pandas as pd
import pytest

# Load the exact repository collector file. This avoids pytest/plugin namespace
# collisions while still exercising the production implementation byte-for-byte.
COLLECTOR_PATH = Path(__file__).resolve().parents[1] / "v182" / "data" / "pre2023_ohlcv_eodhd.py"
_spec = spec_from_file_location("pea_pre2023_ohlcv_eodhd", COLLECTOR_PATH)
assert _spec is not None and _spec.loader is not None
_collector = module_from_spec(_spec)
_spec.loader.exec_module(_collector)

HOLDOUT_START = _collector.HOLDOUT_START
_load_symbols = _collector._load_symbols
_validate_bars = _collector._validate_bars
_validate_window = _collector._validate_window


def test_window_rejects_holdout_start():
    with pytest.raises(ValueError, match="HOLDOUT_LEAK"):
        _validate_window("2022-01-01", "2023-01-01")


def test_window_accepts_full_2010_2022_development_period():
    start, end = _validate_window("2010-01-01", "2022-12-31")
    assert start == pd.Timestamp("2010-01-01", tz="UTC")
    assert end == pd.Timestamp("2022-12-31", tz="UTC")
    assert end < HOLDOUT_START


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
