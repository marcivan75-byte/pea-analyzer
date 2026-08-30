import numpy as np
import pandas as pd
import pytest

from v182.hebdo.backtest_v22_1 import HistoricalPITUnavailable, add_true_forward_returns


def _ohlcv(n=150):
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    close = np.linspace(100.0, 130.0, n)
    return pd.DataFrame({
        "ticker": ["AI.PA"] * n,
        "date": dates,
        "open": close + 0.25,
        "low": close - 1.0,
        "high": close + 1.0,
        "close": close,
    })


def _features(as_of, atr=0.04):
    return pd.DataFrame([{
        "ticker": "AI.PA",
        "as_of_date": as_of,
        "pit_observed_at": pd.Timestamp(as_of, tz="UTC") if pd.Timestamp(as_of).tzinfo is None else as_of,
        "vol_z": 1.0,
        "mom_26w": 0.3,
        "mom_26w_sector": 0.3,
        "rsi_14_hebdo": 60.0,
        "drawdown_4w": -0.02,
        "atr_14_pct": atr,
    }])


def test_true_forward_ledger_uses_intraday_stop_and_j1_open():
    ohlcv = _ohlcv()
    as_of = pd.Timestamp(ohlcv.loc[10, "date"])
    entry_open = float(ohlcv.loc[11, "open"])
    ohlcv.loc[20, "low"] = entry_open * 0.89
    ledger = add_true_forward_returns(_features(as_of), ohlcv)
    assert ledger.loc[0, "entry_date"] == ohlcv.loc[11, "date"]
    assert ledger.loc[0, "entry_price"] == pytest.approx(entry_open)
    assert ledger.loc[0, "execution_policy"] == "NEXT_SESSION_OPEN_J1"
    assert bool(ledger.loc[0, "hit_stop"])
    assert ledger.loc[0, "forward_ret_true_26w"] == pytest.approx(-0.09)


def test_atr_stop_challenger_records_governed_stop():
    ohlcv = _ohlcv()
    as_of = pd.Timestamp(ohlcv.loc[10, "date"])
    ledger = add_true_forward_returns(_features(as_of, atr=0.04), ohlcv, stop_policy="atr")
    assert ledger.loc[0, "stop_policy"] == "atr"
    assert ledger.loc[0, "stop_pct_used"] == pytest.approx(0.10)


def test_historical_features_require_proven_pit_timestamp():
    features = pd.DataFrame([{"ticker": "AI.PA", "as_of_date": "2020-01-15"}])
    with pytest.raises(HistoricalPITUnavailable, match="PIT observation timestamp"):
        add_true_forward_returns(features, _ohlcv())


def test_future_pit_timestamp_is_refused():
    features = pd.DataFrame([{
        "ticker": "AI.PA",
        "as_of_date": "2020-01-15",
        "pit_observed_at": "2020-01-16T00:00:00Z",
    }])
    with pytest.raises(HistoricalPITUnavailable, match="future/invalid"):
        add_true_forward_returns(features, _ohlcv())
