import pandas as pd

from v182.hebdo.tabport_capitulation_confirmation_development import (
    attach_confirmation_strength,
    learn_thresholds,
)


def _confirmed(rows):
    return pd.DataFrame(rows)


def test_attach_confirmation_strength_uses_confirmation_bar_close():
    confirmed = _confirmed([
        {
            "ticker": "AAA.PA",
            "date": "2022-06-06",
            "signal_level": 100.0,
            "drawdown_4w": -0.10,
            "vol_z": 0.2,
            "prob_stop_9": 0.1,
        }
    ])
    ohlcv = pd.DataFrame([
        {"ticker":"AAA.PA","date":"2022-06-06","open":100.0,"high":103.0,"low":99.0,"close":102.0,"volume":1000},
    ])
    out = attach_confirmation_strength(confirmed, ohlcv)
    row = out.iloc[0]
    assert abs(row["j1_gap_pct"] - 0.0) < 1e-12
    assert abs(row["j1_ret_close_pct"] - 0.02) < 1e-12
    assert abs(row["j1_intraday_pct"] - 0.02) < 1e-12
    assert abs(row["j1_close_from_low_pct"] - (102/99-1)) < 1e-12


def test_threshold_learning_is_development_only():
    base = [
        {"date":"2021-01-04","drawdown_4w":-0.20,"vol_z":0.1,"prob_stop_9":0.1,"j1_ret_close_pct":0.01,"j1_intraday_pct":0.01,"j1_close_from_low_pct":0.02},
        {"date":"2022-01-03","drawdown_4w":-0.10,"vol_z":0.3,"prob_stop_9":0.2,"j1_ret_close_pct":0.03,"j1_intraday_pct":0.02,"j1_close_from_low_pct":0.04},
    ]
    holdout_extreme = {
        "date":"2025-01-06","drawdown_4w":-0.99,"vol_z":99.0,"prob_stop_9":0.99,
        "j1_ret_close_pct":0.99,"j1_intraday_pct":0.99,"j1_close_from_low_pct":0.99,
    }
    t1 = learn_thresholds(pd.DataFrame(base))
    t2 = learn_thresholds(pd.DataFrame(base + [holdout_extreme]))
    assert t1 == t2
