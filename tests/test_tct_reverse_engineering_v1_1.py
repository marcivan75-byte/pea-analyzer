import pandas as pd
import pytest

from v182.backtest.tct_reverse_engineering_v1_1 import (
    chronological_split_adaptive,
    infer_research_boundaries,
)


def test_2020_history_uses_current_cache_protocol():
    frame = pd.DataFrame({"date": pd.to_datetime(["2020-01-02", "2021-06-01", "2022-02-01", "2024-02-01", "2025-02-01"])})
    bounds = infer_research_boundaries(frame)
    assert bounds["development_start"] == pd.Timestamp("2022-01-01")
    assert bounds["validation_start"] == pd.Timestamp("2024-01-01")
    assert bounds["holdout_start"] == pd.Timestamp("2025-01-01")


def test_deep_history_keeps_long_protocol():
    frame = pd.DataFrame({"date": pd.to_datetime(["2010-01-04", "2018-12-31", "2019-01-02", "2023-01-03", "2025-01-02"])})
    bounds = infer_research_boundaries(frame)
    assert bounds["development_start"] == pd.Timestamp("2019-01-01")
    assert bounds["validation_start"] == pd.Timestamp("2023-01-01")
    assert bounds["holdout_start"] == pd.Timestamp("2025-01-01")


def test_adaptive_split_contains_four_blocks_when_history_supports_them():
    dates = pd.to_datetime([
        "2020-06-01", "2021-06-01", "2022-06-01", "2023-06-01",
        "2024-06-01", "2025-06-01", "2026-06-01",
    ])
    frame = pd.DataFrame({"date": dates})
    split = chronological_split_adaptive(frame, purge_sessions=0)
    assert {"DISCOVERY", "DEVELOPMENT", "VALIDATION", "HOLDOUT"}.issubset(set(split["research_split"]))
    assert split["research_split_protocol"].nunique() == 1


def test_date_alias_as_of_date_is_supported():
    frame = pd.DataFrame({"as_of_date": pd.to_datetime([
        "2020-01-02", "2022-01-03", "2024-01-03", "2025-01-03"
    ])})
    bounds = infer_research_boundaries(frame)
    assert bounds["holdout_start"] == pd.Timestamp("2025-01-01")


def test_date_alias_session_date_is_supported():
    frame = pd.DataFrame({"session_date": pd.to_datetime([
        "2020-01-02", "2022-01-03", "2024-01-03", "2025-01-03"
    ])})
    split = chronological_split_adaptive(frame, purge_sessions=0)
    assert "HOLDOUT" in set(split["research_split"])


def test_missing_date_column_fails_closed():
    frame = pd.DataFrame({"x": [1, 2, 3, 4]})
    with pytest.raises(ValueError, match="SPLIT_DATE_COLUMN_MISSING"):
        infer_research_boundaries(frame)


def test_too_short_history_fails_closed():
    frame = pd.DataFrame({"date": pd.to_datetime(["2023-01-03", "2024-01-03", "2025-01-03"])})
    with pytest.raises(ValueError, match="INSUFFICIENT_HISTORY_FOR_4_BLOCK_SPLIT"):
        infer_research_boundaries(frame)
