from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from v182.hebdo.backtest_v22_1 import HistoricalPITUnavailable, add_true_forward_returns
from v182.hebdo.backtest_v22_1_fast import add_true_forward_returns_fast


def _frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2020-01-01", periods=220)
    rows = []
    for ticker, base in [("AAA", 100.0), ("BBB", 50.0)]:
        for i, d in enumerate(dates):
            close = base * (1.0 + 0.001 * i)
            open_ = close * (1.0 + (0.001 if i % 3 == 0 else -0.0005))
            low = min(open_, close) * (0.985 if i not in (80, 150) else 0.88)
            high = max(open_, close) * 1.015
            rows.append({"ticker": ticker, "date": d, "open": open_, "low": low, "high": high, "close": close})
    ohlcv = pd.DataFrame(rows)

    feature_rows = []
    for ticker in ("AAA", "BBB"):
        # Includes observations with complete 126d labels and near-tail observations
        # where only shorter horizons are complete.
        selected = dates[30:90:7].append(dates[100:145:9]).append(dates[185:216:7])
        for d in selected:
            feature_rows.append(
                {
                    "ticker": ticker,
                    "as_of_date": d,
                    "pit_observed_at": pd.Timestamp(d).tz_localize("UTC"),
                    "atr_14_pct": 0.025,
                    "vol_z": 1.0,
                    "mom_26w": 0.1,
                    "rsi_14_hebdo": 55.0,
                    "drawdown_4w": -0.03,
                    "close": 100.0,
                    "sma200": 95.0,
                }
            )
    return pd.DataFrame(feature_rows), ohlcv


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().sort_values(["ticker", "as_of_date"]).reset_index(drop=True)
    return out


def _assert_equivalent(slow: pd.DataFrame, fast: pd.DataFrame) -> None:
    slow = _normalise(slow)
    fast = _normalise(fast)
    assert list(slow.columns) == list(fast.columns)
    assert len(slow) == len(fast)

    numeric = (
        "entry_price",
        "stop_pct_used",
        "forward_ret_true_1w",
        "forward_ret_true_2w",
        "forward_ret_true_4w",
        "forward_ret_true_13w",
        "forward_ret_true_26w",
        "mae",
        "mfe",
    )
    for col in numeric:
        np.testing.assert_allclose(
            pd.to_numeric(slow[col], errors="coerce"),
            pd.to_numeric(fast[col], errors="coerce"),
            rtol=0,
            atol=1e-12,
            equal_nan=True,
        )

    for col in ("ticker", "execution_policy", "stop_policy"):
        pd.testing.assert_series_equal(slow[col], fast[col], check_names=False, check_dtype=False)

    # Datetime resolution (us vs ns) is an internal pandas/numpy representation;
    # governance requires equality of timestamps, not equality of storage units.
    for col in ("as_of_date", "signal_market_date", "entry_date", "label_end_date_26w"):
        left = pd.to_datetime(slow[col], errors="coerce").to_numpy(dtype="datetime64[ns]")
        right = pd.to_datetime(fast[col], errors="coerce").to_numpy(dtype="datetime64[ns]")
        np.testing.assert_array_equal(left, right)

    assert slow["hit_stop"].astype("boolean").equals(fast["hit_stop"].astype("boolean"))
    assert slow["day_stop"].astype("Int64").equals(fast["day_stop"].astype("Int64"))


def test_fast_forward_ledger_matches_reference_fixed_stop():
    features, ohlcv = _frames()
    _assert_equivalent(
        add_true_forward_returns(features, ohlcv, stop_pct=0.09, stop_policy="fixed"),
        add_true_forward_returns_fast(features, ohlcv, stop_pct=0.09, stop_policy="fixed"),
    )


def test_fast_forward_ledger_matches_reference_atr_stop():
    features, ohlcv = _frames()
    features = features.copy()
    features.loc[features.index % 3 == 0, "atr_14_pct"] = 0.01
    features.loc[features.index % 3 == 1, "atr_14_pct"] = 0.03
    features.loc[features.index % 3 == 2, "atr_14_pct"] = 0.08
    _assert_equivalent(
        add_true_forward_returns(features, ohlcv, stop_policy="atr"),
        add_true_forward_returns_fast(features, ohlcv, stop_policy="atr"),
    )


def test_fast_atr_invalid_is_fail_fatal_like_reference():
    features, ohlcv = _frames()
    features = features.copy()
    features.loc[0, "atr_14_pct"] = np.nan
    with pytest.raises(HistoricalPITUnavailable, match="invalid atr_14_pct"):
        add_true_forward_returns_fast(features, ohlcv, stop_policy="atr")
