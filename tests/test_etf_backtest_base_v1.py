import json
from pathlib import Path

import pandas as pd

from v182.backtest.etf_backtest_base_v1 import _merge_append_only, _normalise_history, audit_history

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "ETF_BACKTEST_BASE_V1.json").read_text(encoding="utf-8"))


def _frame(rows=800):
    dates = pd.bdate_range("2020-01-01", periods=rows)
    close = pd.Series(range(100, 100 + rows), dtype=float)
    return pd.DataFrame({
        "Date": dates,
        "Open": close.values - 0.2,
        "High": close.values + 0.5,
        "Low": close.values - 0.5,
        "Close": close.values,
        "Adj Close": close.values,
        "Volume": 100000,
        "Dividends": 0.0,
        "Stock Splits": 0.0,
    })


def test_policy_is_long_history_and_not_rolling():
    assert CFG["history"]["start"] == "2010-01-01"
    assert CFG["history"]["rolling_trim_forbidden"] is True
    assert CFG["source"]["auto_adjust"] is False
    assert CFG["history"]["preserve_volume"] is True
    assert CFG["history"]["preserve_dividends"] is True
    assert CFG["history"]["preserve_splits"] is True


def test_current_universe_reconstruction_never_promotes():
    pit = CFG["pit_governance"]
    assert pit["survivorship_bias_protection_required"] is True
    assert pit["current_universe_reconstruction_promotion_eligible"] is False
    assert pit["membership_start_required_for_promotion"] is True
    assert pit["pea_eligibility_as_of_required_for_promotion"] is True


def test_exchange_local_daily_date_is_not_shifted_to_previous_utc_day():
    idx = pd.DatetimeIndex([pd.Timestamp("2026-01-05 00:00:00", tz="Europe/Paris")], name="Date")
    frame = pd.DataFrame({
        "Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5],
        "Adj Close": [100.5], "Volume": [1000], "Dividends": [0.0], "Stock Splits": [0.0],
    }, index=idx)
    normalised = _normalise_history(frame)
    assert str(normalised.loc[0, "date"].date()) == "2026-01-05"


def test_good_history_passes_quality_and_has_mt_depth():
    q = audit_history("FR0000000001", "TEST.PA", _frame(), CFG)
    assert q.quality_pass is True
    assert q.mt_756_sessions_available is True
    assert q.volume_coverage_pct == 100.0
    assert q.ohlc_invariant_violations == 0


def test_bad_volume_and_ohlc_fail_quality():
    frame = _frame()
    frame.loc[0, "Volume"] = -1
    frame.loc[1, "High"] = frame.loc[1, "Low"] - 1
    q = audit_history("FR0000000001", "TEST.PA", frame, CFG)
    assert q.quality_pass is False
    assert q.negative_volume_rows == 1
    assert q.ohlc_invariant_violations == 1


def test_append_only_does_not_silently_rewrite_old_observation():
    old = _normalise_history(_frame(10))
    fresh = old.copy()
    fresh.loc[fresh.index[0], "close"] = 9999.0
    future = fresh.iloc[[-1]].copy()
    future["date"] = future["date"] + pd.Timedelta(days=5)
    fresh = pd.concat([fresh, future], ignore_index=True)
    merged = _merge_append_only(old, fresh)
    original_date = old.loc[0, "date"]
    assert float(merged.loc[merged["date"] == original_date, "close"].iloc[0]) == float(old.loc[0, "close"])
    assert merged["date"].max() > old["date"].max()
