import numpy as np
import pandas as pd

from v182.backtest.v21_8_1_backtest_B_v2 import (
    compute_mae_mfe,
    compute_true_26w_pnl,
    detect_B_v2,
    run_backtest_B_v2,
)


def _signal_frame(periods: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=periods, freq="B")
    close = np.full(periods, 100.0)
    volume = np.array([90.0 + (i % 11) * 2.0 for i in range(periods)])
    # First eligible z-score day: 20 prior observations exist.
    close[20] = 97.0
    volume[20] = 500.0
    close[21:] = 97.5
    high = np.maximum(close + 2.0, 100.0)
    low = close - 2.0
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_true_pnl_hits_intraday_stop():
    idx = pd.date_range("2024-01-02", periods=12, freq="D")
    hist = pd.DataFrame(
        {
            "low": [99, 98, 97, 96, 95, 94, 93, 92, 91.5, 91.2, 90.0, 95],
            "high": [101] * 12,
            "close": [100, 99, 98, 97, 96, 95, 94, 93, 92, 92, 91, 96],
        },
        index=idx,
    )
    pnl, hit, day_stop, exit_price = compute_true_26w_pnl(100.0, hist, 0.09)
    assert pnl == -0.09
    assert hit is True
    assert day_stop == 10
    assert exit_price == 91.0


def test_mae_mfe_log_full_forward_window_even_after_stop():
    hist = pd.DataFrame(
        {
            "low": [99.0, 90.0, 88.0, 95.0, 96.0],
            "high": [101.0, 102.0, 103.0, 120.0, 110.0],
            "close": [100.0, 91.0, 95.0, 118.0, 108.0],
        }
    )
    mae, mfe = compute_mae_mfe(100.0, hist)
    assert mae == -0.12
    assert mfe == 0.20


def test_b1_zscore_and_b2_daily_detection():
    df = _signal_frame()
    out = detect_B_v2(df)
    signal_day = df.index[20]
    next_day = df.index[21]
    assert bool(out.loc[signal_day, "B1_vol_v2"])
    assert bool(out.loc[signal_day, "B1_vol"])
    assert float(out.loc[signal_day, "vol_z"]) > 3.0
    assert bool(out.loc[next_day, "B2_daily"])
    assert out.loc[signal_day, "B_signal_type"] == "B1_VOL_V2"
    assert out.loc[next_day, "B_signal_type"] == "B2_DAILY"


def test_backtest_records_real_stop_date_and_excursions():
    df = _signal_frame(50)
    signal_day = df.index[20]
    # Entry at 97. Stop price = 88.27; hit on the 10th forward observation.
    stop_index = 20 + 10
    df.loc[df.index[stop_index], "low"] = 80.0
    df.loc[df.index[35], "high"] = 120.0
    out = run_backtest_B_v2(df, stop_pct=0.09, forward_days=20)
    b1 = out[out["B1_vol_v2"]].iloc[0]
    assert bool(b1["hit_stop"])
    # hist_forward starts at entry+1, so source index entry+10 is day_stop 9.
    assert int(b1["day_stop"]) == 9
    assert pd.Timestamp(b1["exit_date"]) == df.index[stop_index]
    assert float(b1["pnl_true"]) == -0.09
    assert float(b1["mae"]) < -0.09
    assert float(b1["mfe"]) > 0.20
