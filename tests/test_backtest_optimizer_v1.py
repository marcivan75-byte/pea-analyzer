from __future__ import annotations

import numpy as np
import pandas as pd

from v182.decision.backtest_optimizer_v1 import BacktestOptimizer, OptimizerConfig, attach_forward_returns


def _history(n_dates: int = 18, n_assets: int = 50) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2025-01-03", periods=n_dates, freq="14D")
    rows = []
    latent = rng.normal(size=n_assets)
    for d_i, date in enumerate(dates):
        for i in range(n_assets):
            momentum = np.clip(50 + 22 * latent[i] + rng.normal(0, 5), 0, 100)
            quality = np.clip(50 + rng.normal(0, 18), 0, 100)
            # Price path is intentionally driven by momentum, so the optimiser
            # has a real signal to discover without seeing future rows directly.
            price = 100 * np.exp(0.0025 * d_i * latent[i] + 0.0005 * d_i)
            rows.append({
                "__instrument_id": f"ASSET{i:03d}",
                "__snapshot_date": date,
                "__price": price,
                "Score V10 /100": 0.6 * quality + 0.4 * momentum,
                "score_momentum_100": momentum,
                "score_quality_100": quality,
                "score_catalyst_100": 50.0,
                "score_risk_100": 50.0,
                "score_value_100": 50.0,
                "score_expectancy_100": 50.0,
                "score_structure_100": 50.0,
                "score_sector_100": 50.0,
                "score_fiscal_100": 50.0,
            })
    return pd.DataFrame(rows)


def test_forward_returns_only_use_later_snapshot():
    df = pd.DataFrame({
        "__instrument_id": ["X", "X", "X"],
        "__snapshot_date": pd.to_datetime(["2025-01-01", "2025-01-29", "2025-02-26"]),
        "__price": [100.0, 110.0, 99.0],
    })
    out = attach_forward_returns(df, 28, 2)
    assert abs(out.loc[0, "__forward_return"] - 0.10) < 1e-12
    assert abs(out.loc[1, "__forward_return"] + 0.10) < 1e-12
    assert pd.isna(out.loc[2, "__forward_return"])


def test_insufficient_history_is_guarded():
    raw = attach_forward_returns(_history(n_dates=6), 14, 2)
    cfg = OptimizerConfig(horizon_days=14, horizon_tolerance_days=2, min_snapshots=12, min_instruments_per_snapshot=20)
    result = BacktestOptimizer(cfg).optimize(raw)
    assert result.status == "INSUFFICIENT_HISTORY"
    assert result.audit["production_weights_modified"] is False


def test_optimizer_is_deterministic_and_bounded():
    raw = attach_forward_returns(_history(), 14, 2)
    cfg = OptimizerConfig(
        horizon_days=14,
        horizon_tolerance_days=2,
        min_snapshots=12,
        min_test_snapshots=4,
        min_instruments_per_snapshot=20,
        candidate_count=250,
        top_k=10,
        min_oos_improvement=-1.0,
        max_drawdown_worsening=1.0,
    )
    a = BacktestOptimizer(cfg).optimize(raw)
    b = BacktestOptimizer(cfg).optimize(raw)
    assert a.recommended_weights == b.recommended_weights
    assert abs(sum(a.recommended_weights.values()) - 1.0) < 1e-9
    assert max(a.recommended_weights.values()) <= cfg.max_single_weight + 1e-9
    assert a.audit["production_weights_modified"] is False
