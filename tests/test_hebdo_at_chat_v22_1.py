import numpy as np
import pandas as pd
import pytest

from v182.hebdo.hebdo_at_chat_v22 import MarketRegime
from v182.hebdo.hebdo_at_chat_v22_1 import (
    HebdoV221Blocked,
    apply_four_week_exit,
    apply_quality_filter,
    build_dashboard,
    double_sector_selection,
    volatility_target_weights,
)
from v182.hebdo.mae_predictor import MAEPredictor, apply_mae_filter


def test_mae_predictor_excludes_high_stop_risk():
    frame = pd.DataFrame([
        {"vol_z": 4.5, "drawdown_4w": -0.15, "close": 90.0, "sma200": 100.0, "atr_14_pct": 0.04}
    ])
    out = MAEPredictor().predict_batch(frame)
    assert out.loc[0, "EXCLU_MAE"]
    assert out.loc[0, "stop_prob"] > 0.45


def test_mae_filter_blocks_missing_without_imputation():
    frame = pd.DataFrame([{"vol_z": 2.0}])
    out = apply_mae_filter(frame)
    assert out.loc[0, "mae_status"] == "BLOCK_DATA_MAE"
    assert pd.isna(out.loc[0, "stop_prob"])


def test_quality_filter_is_fail_closed_and_excludes_bad_quality():
    frame = pd.DataFrame([
        {"roe": 0.04, "debt_to_equity": 1.6},
        {"roe": 0.08, "debt_to_equity": 2.0},
        {"roe": np.nan, "debt_to_equity": 1.0},
    ])
    out = apply_quality_filter(frame)
    assert out.loc[0, "quality_status"] == "EXCLU_QUALITE"
    assert out.loc[1, "quality_status"] == "OK"
    assert out.loc[2, "quality_status"] == "BLOCK_DATA_QUALITY"


def test_double_sector_selection_keeps_max_two_per_sector():
    rows = []
    for sector in ("A", "B", "C"):
        for i in range(4):
            rows.append({
                "sector": sector,
                "governed_score": 10 - i,
                "mom_26w_sector": 5 - i,
                "selection_status": "OK",
                "quality_status": "OK",
            })
    out = double_sector_selection(pd.DataFrame(rows), max_tct=6, max_ct=4)
    chosen = out[out["hebdo_bucket"].isin(["TCT", "CT"])]
    assert int(chosen.groupby("sector").size().max()) <= 2
    assert int((out["hebdo_bucket"] == "TCT").sum()) <= 6


def test_vol_targeting_inverse_atr_and_crash_cash():
    frame = pd.DataFrame({"atr_14_pct": [0.02, 0.04]})
    crash = MarketRegime("CRASH", 0.5, -0.04)
    w = volatility_target_weights(frame, crash)
    assert w.sum() == pytest.approx(0.8)
    assert w.iloc[0] == pytest.approx(2 * w.iloc[1])


def test_vol_targeting_refuses_missing_atr():
    frame = pd.DataFrame({"atr_14_pct": [0.02, np.nan]})
    normal = MarketRegime("NORMAL", 1.0, 0.01)
    with pytest.raises(HebdoV221Blocked):
        volatility_target_weights(frame, normal)


def test_four_week_exit_only_for_tct_negative_sector_momentum():
    frame = pd.DataFrame({
        "hebdo_bucket": ["TCT", "CT", "TCT"],
        "holding_days": [20, 25, 19],
        "mom_26w_sector": [-0.1, -0.2, -0.3],
    })
    out = apply_four_week_exit(frame)
    assert bool(out.loc[0, "exit_4w_signal"])
    assert not bool(out.loc[1, "exit_4w_signal"])
    assert not bool(out.loc[2, "exit_4w_signal"])


def test_dashboard_computes_ic_decay_and_true_metrics():
    n = 40
    score = pd.Series(np.arange(n, dtype=float))
    ret = score / 1000.0
    frame = pd.DataFrame({
        "governed_score": score,
        "forward_ret_true_1w": ret,
        "forward_ret_true_4w": ret,
        "forward_ret_true_26w": ret,
        "mae": np.full(n, -0.04),
        "hit_stop": [False] * 36 + [True] * 4,
        "selection_status": ["OK"] * n,
        "quality_status": ["OK"] * n,
        "mae_status": ["OK"] * n,
        "hebdo_bucket": ["TCT"] * 20 + ["CT"] * 20,
    })
    dashboard = build_dashboard(frame, MarketRegime("NORMAL", 1.0, 0.01), turnover=0.20)
    assert dashboard["ic_1w"] == pytest.approx(1.0)
    assert dashboard["ic_4w"] == pytest.approx(1.0)
    assert dashboard["hit_rate_26w_true"] == pytest.approx(39 / 40)
    assert dashboard["mae_mean"] == pytest.approx(-0.04)
    assert dashboard["stop_rate"] == pytest.approx(0.10)
