import numpy as np
import pandas as pd
import pytest

from v182.hebdo.hebdo_at_chat_v22 import MarketRegime
from v182.hebdo.hebdo_at_chat_v22_1 import (
    HebdoV221Blocked,
    adaptive_atr_stop_pct,
    apply_correlation_guard,
    apply_earnings_filter,
    apply_four_week_exit,
    apply_quality_filter,
    build_dashboard,
    double_sector_selection,
    volatility_target_weights,
)
from v182.hebdo.mae_predictor import (
    MAEDataUnavailable,
    MAEPredictor,
    apply_mae_filter,
    train_stop_model,
)


def test_mae_predictor_excludes_high_stop_risk():
    frame = pd.DataFrame([{"vol_z": 4.5, "drawdown_4w": -0.15, "close": 90.0, "sma200": 100.0, "atr_14_pct": 0.04}])
    out = MAEPredictor().predict_batch(frame)
    assert out.loc[0, "EXCLU_MAE"]
    assert out.loc[0, "stop_prob"] > 0.45


def test_mae_filter_blocks_missing_without_imputation():
    out = apply_mae_filter(pd.DataFrame([{"vol_z": 2.0}]))
    assert out.loc[0, "mae_status"] == "BLOCK_DATA_MAE"
    assert pd.isna(out.loc[0, "stop_prob"])


def test_mae_filter_requires_trained_artifact_when_requested():
    frame = pd.DataFrame([{"vol_z": 2.0, "drawdown_4w": -0.05, "close": 105.0, "sma200": 100.0, "atr_14_pct": 0.02}])
    with pytest.raises(MAEDataUnavailable, match="trained artifact required"):
        apply_mae_filter(frame, require_trained=True)


def test_trained_mae_model_uses_temporal_validation():
    rng = np.random.default_rng(7)
    n = 220
    vol_z = rng.normal(2.5, 1.2, n)
    dd = rng.normal(-0.07, 0.05, n)
    atr = rng.uniform(0.01, 0.06, n)
    close = np.full(n, 100.0)
    sma200 = np.where(np.arange(n) % 3 == 0, 105.0, 95.0)
    latent = vol_z + (-dd * 8.0) + atr * 10.0 + (close < sma200) * 0.8
    history = pd.DataFrame({
        "as_of_date": pd.date_range("2020-01-03", periods=n, freq="W-FRI"),
        "vol_z": vol_z,
        "drawdown_4w": dd,
        "close": close,
        "sma200": sma200,
        "atr_14_pct": atr,
        "hit_stop": latent > np.median(latent),
    })
    artifact = train_stop_model(history)
    assert artifact["n_train"] >= 150
    assert artifact["n_validation"] >= 30
    assert artifact["validation_brier"] >= 0


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


def test_earnings_filter_excludes_known_event_within_three_days():
    frame = pd.DataFrame({"selection_status": ["OK", "OK"], "days_to_earnings": [2, 6]})
    out = apply_earnings_filter(frame)
    assert out.loc[0, "selection_status"] == "EXCLU_EARNINGS"
    assert out.loc[0, "earnings_status"] == "EXCLU_EARNINGS"
    assert out.loc[1, "selection_status"] == "OK"


def test_double_sector_selection_keeps_max_two_per_sector():
    rows = []
    for sector in ("A", "B", "C"):
        for i in range(4):
            rows.append({"ticker": f"{sector}{i}", "sector": sector, "governed_score": 10 - i, "mom_26w_sector": 5 - i, "selection_status": "OK", "quality_status": "OK"})
    out = double_sector_selection(pd.DataFrame(rows), max_tct=6, max_ct=4)
    chosen = out[out["hebdo_bucket"].isin(["TCT", "CT"])]
    assert int(chosen.groupby("sector").size().max()) <= 2
    assert int((out["hebdo_bucket"] == "TCT").sum()) <= 6


def test_hysteresis_retains_incumbent_unless_challenger_clears_margin():
    frame = pd.DataFrame([
        {"ticker": "OLD", "sector": "A", "governed_score": 1.00, "mom_26w_sector": 1.0, "selection_status": "OK", "quality_status": "OK"},
        {"ticker": "NEW", "sector": "B", "governed_score": 1.10, "mom_26w_sector": 1.1, "selection_status": "OK", "quality_status": "OK"},
    ])
    out = double_sector_selection(frame, max_tct=1, max_ct=1, previous_tct={"OLD"}, replacement_margin=0.15)
    assert out.loc[out["ticker"].eq("OLD"), "hebdo_bucket"].iloc[0] == "TCT"


def test_correlation_guard_demotes_redundant_tct():
    n = 60
    base = np.linspace(-0.02, 0.02, n)
    returns = pd.DataFrame({"AAA": base, "BBB": base * 1.01, "CCC": base[::-1]})
    frame = pd.DataFrame({
        "ticker": ["AAA", "BBB", "CCC"],
        "hebdo_bucket": ["TCT", "TCT", "TCT"],
        "governed_score": [3.0, 2.0, 1.0],
    })
    out = apply_correlation_guard(frame, returns, threshold=0.80, max_ct=20)
    assert out.loc[out["ticker"].eq("BBB"), "hebdo_bucket"].iloc[0] == "CT"
    assert out.loc[out["ticker"].eq("BBB"), "correlation_status"].iloc[0] == "DEMOTED_CORR"


def test_vol_targeting_inverse_atr_and_crash_cash():
    frame = pd.DataFrame({"atr_14_pct": [0.02, 0.04]})
    crash = MarketRegime("CRASH", 0.5, -0.04)
    w = volatility_target_weights(frame, crash)
    assert w.sum() == pytest.approx(0.8)
    assert w.iloc[0] == pytest.approx(2 * w.iloc[1])


def test_vol_targeting_refuses_missing_atr():
    with pytest.raises(HebdoV221Blocked):
        volatility_target_weights(pd.DataFrame({"atr_14_pct": [0.02, np.nan]}), MarketRegime("NORMAL", 1.0, 0.01))


def test_adaptive_atr_stop_is_floored_and_capped():
    assert adaptive_atr_stop_pct(0.02) == pytest.approx(0.06)
    assert adaptive_atr_stop_pct(0.04) == pytest.approx(0.10)
    assert adaptive_atr_stop_pct(0.06) == pytest.approx(0.12)


def test_four_week_exit_negative_momentum_and_partial_profit_lock():
    frame = pd.DataFrame({
        "hebdo_bucket": ["TCT", "TCT", "CT", "TCT"],
        "holding_days": [20, 20, 25, 19],
        "mom_26w_sector": [-0.1, 0.2, -0.2, -0.3],
        "pnl_since_entry": [0.05, 0.10, 0.20, 0.20],
    })
    out = apply_four_week_exit(frame)
    assert out.loc[0, "exit_action"] == "EXIT_FULL_MOMENTUM"
    assert out.loc[1, "exit_action"] == "TAKE_50_AND_BE"
    assert out.loc[1, "partial_exit_fraction"] == pytest.approx(0.5)
    assert bool(out.loc[1, "move_stop_to_breakeven"])
    assert out.loc[2, "exit_action"] == "HOLD"
    assert out.loc[3, "exit_action"] == "HOLD"


def test_dashboard_uses_universe_for_ic_but_portfolio_for_pnl_metrics_and_costs():
    n = 40
    score = pd.Series(np.arange(n, dtype=float))
    ret = score / 1000.0
    frame = pd.DataFrame({
        "governed_score": score,
        "forward_ret_true_1w": ret,
        "forward_ret_true_4w": ret,
        "forward_ret_true_26w": np.r_[np.full(20, 0.05), np.full(20, -0.20)],
        "mae": np.r_[np.full(20, -0.04), np.full(20, -0.20)],
        "hit_stop": [False] * 18 + [True] * 2 + [True] * 20,
        "selection_status": ["OK"] * n,
        "quality_status": ["OK"] * n,
        "mae_status": ["OK"] * n,
        "hebdo_bucket": ["TCT"] * 10 + ["CT"] * 10 + ["NONE"] * 20,
        "portfolio_weight": np.r_[np.full(20, 0.05), np.zeros(20)],
        "adv_20_eur": np.full(n, 2_000_000.0),
    })
    dashboard = build_dashboard(frame, MarketRegime("NORMAL", 1.0, 0.01), turnover=0.20, gross_alpha_period=0.01)
    assert dashboard["rows_universe"] == 40
    assert dashboard["rows_portfolio"] == 20
    assert dashboard["hit_rate_26w_true"] == pytest.approx(1.0)
    assert dashboard["mae_mean"] == pytest.approx(-0.04)
    assert dashboard["stop_rate"] == pytest.approx(0.10)
    assert dashboard["gross_exposure"] == pytest.approx(1.0)
    assert dashboard["estimated_transaction_cost_period"] == pytest.approx(0.0003)
    assert dashboard["net_alpha_after_estimated_cost"] == pytest.approx(0.0097)
