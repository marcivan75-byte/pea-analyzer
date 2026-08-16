import numpy as np
import pandas as pd

from v182.risk.beta_metrics import build_common_benchmark


def _prices_from_returns(returns: pd.Series) -> pd.Series:
    return 100.0 * (1.0 + returns).cumprod()


def test_robust_benchmark_neutralizes_sparse_cross_section_outliers():
    idx = pd.date_range("2022-01-03", periods=180, freq="B")
    action_prices = {}
    for number in range(60):
        returns = pd.Series(0.001, index=idx)
        if number < 2:
            returns.iloc[120] = 4.0
        action_prices[f"T{number:03d}"] = _prices_from_returns(returns)

    benchmark, diag = build_common_benchmark(
        action_prices,
        min_sessions=126,
        min_constituents=20,
        min_daily_fraction=0.20,
    )

    assert benchmark is not None
    assert diag["status"] == "OK"
    assert diag["label"] == "PEA_ACTION_ROBUST_EQUAL_WEIGHT_PROXY_V2"
    assert "WINSORIZED" in diag["method"]
    assert float(benchmark.abs().max()) < 0.01


def test_benchmark_fails_closed_if_broad_move_remains_implausible():
    idx = pd.date_range("2022-01-03", periods=180, freq="B")
    action_prices = {}
    for number in range(60):
        returns = pd.Series(0.001, index=idx)
        returns.iloc[120] = 0.30
        action_prices[f"T{number:03d}"] = _prices_from_returns(returns)

    benchmark, diag = build_common_benchmark(
        action_prices,
        min_sessions=126,
        min_constituents=20,
        min_daily_fraction=0.20,
        max_abs_daily_return=0.15,
    )

    assert benchmark is None
    assert diag["status"] == "BENCHMARK_QC_FAILED_EXTREME_DAILY_RETURN"
    assert diag["max_abs_daily_return"] > diag["allowed_max_abs_daily_return"]


def test_benchmark_requires_breadth_relative_to_eligible_universe():
    idx = pd.date_range("2022-01-03", periods=180, freq="B")
    action_prices = {}
    for number in range(100):
        start = 0 if number < 30 else 100
        returns = pd.Series(0.001, index=idx[start:])
        action_prices[f"T{number:03d}"] = _prices_from_returns(returns)

    benchmark, diag = build_common_benchmark(
        action_prices,
        min_sessions=60,
        min_constituents=20,
        min_daily_fraction=0.50,
    )

    assert benchmark is not None
    assert diag["required_daily_constituents"] == 50
    assert benchmark.index.min() >= idx[100]
    assert diag["min_observed_daily_constituents"] >= 50


def test_benchmark_diagnostics_are_finite_and_bounded():
    idx = pd.date_range("2022-01-03", periods=180, freq="B")
    rng = np.random.default_rng(17)
    action_prices = {}
    for number in range(80):
        returns = pd.Series(rng.normal(0.0003, 0.012, len(idx)), index=idx)
        action_prices[f"T{number:03d}"] = _prices_from_returns(returns)

    benchmark, diag = build_common_benchmark(action_prices, min_sessions=126, min_constituents=20)

    assert benchmark is not None
    assert np.isfinite(diag["max_abs_daily_return"])
    assert np.isfinite(diag["p99_abs_daily_return"])
    assert diag["max_abs_daily_return"] <= diag["allowed_max_abs_daily_return"]
