import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.backtest.risk_beta_pit_oos import (
    _period_evaluation,
    attach_pit_risk_features,
    beta_only_multiplier,
)


def test_protocol_is_locked_and_holdout_closed():
    root = Path(__file__).resolve().parents[1]
    protocol = json.loads((root / "config" / "BETA_RISK_PIT_OOS_PROTOCOL.json").read_text(encoding="utf-8"))
    assert protocol["locked_before_results"] is True
    assert protocol["final_holdout_opened"] is False
    assert protocol["final_holdout_start"] == "2026-02-10"
    assert protocol["tested_intervention"]["selection_changes"] is False
    assert protocol["tested_intervention"]["entry_exit_changes"] is False
    assert protocol["tested_intervention"]["stop_changes"] is False
    assert protocol["governance"]["no_parameter_optimization_after_results"] is True


def test_beta_only_multiplier_is_pre_registered_formula():
    assert beta_only_multiplier(0.8, 0.9) == 1.0
    assert beta_only_multiplier(1.2, None) == round(1 / 1.2, 6)
    assert beta_only_multiplier(1.1, 2.0) == 0.5
    assert beta_only_multiplier(None, None) == 1.0


def test_pit_feature_attachment_never_uses_future_prices():
    idx = pd.date_range("2023-01-02", periods=420, freq="B")
    rng = np.random.default_rng(12)
    benchmark = pd.Series(rng.normal(0.0003, 0.01, len(idx)), index=idx)
    etf_returns = 1.4 * benchmark
    prices = 100 * (1 + etf_returns).cumprod()
    history = pd.DataFrame({"Close": prices}, index=idx)
    signal = idx[319]
    trades = pd.DataFrame([
        {
            "isin": "ETF1",
            "signal_date": signal.date().isoformat(),
            "period": "VALIDATION_OOS",
            "net_return": -0.05,
        }
    ])
    out = attach_pit_risk_features(trades, {"ETF1": history}, benchmark)
    assert pd.Timestamp(out.loc[0, "risk_lookahead_guard_asset_max"]) <= signal
    assert pd.Timestamp(out.loc[0, "risk_lookahead_guard_benchmark_max"]) <= signal
    assert out.loc[0, "risk_beta_252d"] > 1.3
    assert out.loc[0, "risk_beta_only_multiplier"] < 1.0


def test_promotion_gate_requires_all_pre_registered_checks():
    frame = pd.DataFrame(
        {
            "period": ["VALIDATION_OOS"] * 10,
            "net_return": [0.04, 0.03, 0.025, 0.02, 0.015, 0.01, -0.04, -0.05, -0.06, -0.07],
            "risk_adjusted_net_return": [0.04, 0.03, 0.025, 0.02, 0.015, 0.01, -0.02, -0.025, -0.03, -0.035],
            "risk_beta_252d": [1.0] * 10,
        }
    )
    gates = {
        "minimum_trades_each_oos_period": 8,
        "minimum_beta_coverage": 0.80,
        "minimum_risk_adjusted_improvement": 0.05,
        "minimum_expectancy_retention": 0.95,
        "require_p05_tail_improvement": True,
        "require_sequence_max_drawdown_improvement": True,
    }
    result = _period_evaluation(frame, "VALIDATION_OOS", gates)
    assert result["checks"]["minimum_trades"] is True
    assert result["checks"]["minimum_beta_coverage"] is True
    assert result["checks"]["p05_tail_improvement"] is True
    assert result["checks"]["sequence_max_drawdown_improvement"] is True
    assert isinstance(result["pass"], bool)
