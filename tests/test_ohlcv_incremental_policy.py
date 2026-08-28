from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_weekly_runner_sets_10d_incremental_ohlcv_window():
    text = (ROOT / "src/v182/reporting/weekly_operational_runner_v4_4.py").read_text(encoding="utf-8")
    assert "PEA_YF_INCREMENTAL_PERIOD" in text
    assert "10d" in text
    assert "ohlcv_incremental_policy" in text
    assert "write_ohlcv_policy" in text


def test_policy_module_does_not_force_full_rebuild():
    text = (ROOT / "src/v182/sources/ohlcv_incremental_policy.py").read_text(encoding="utf-8")
    assert 'DEFAULT_INCREMENTAL_PERIOD = "10d"' in text
    assert "PEA_YF_FORCE_FULL_HISTORY" in text
    assert "criteria_changed" in text
