import json
from pathlib import Path

import pandas as pd

from v182.backtest.risk_beta_regime_pit_oos import regime_multiplier


def _protocol() -> dict:
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "config" / "BETA_RISK_REGIME_PIT_OOS_PROTOCOL.json").read_text(encoding="utf-8"))


def test_protocol_is_pre_registered_and_holdout_closed():
    protocol = _protocol()
    assert protocol["locked_before_results"] is True
    assert protocol["final_holdout_opened"] is False
    assert protocol["final_holdout_start"] == "2026-02-10"
    assert protocol["primary_intervention"]["selection_changes"] is False
    assert protocol["primary_intervention"]["entry_exit_changes"] is False
    assert protocol["primary_intervention"]["stop_changes"] is False
    assert protocol["primary_intervention"]["score_changes"] is False
    assert protocol["governance"]["no_parameter_optimization_after_results"] is True


def test_trigger_requires_every_pre_registered_condition():
    protocol = _protocol()
    multiplier, fired, reasons = regime_multiplier(
        downside_beta_252d=1.50,
        r2_252d=0.50,
        beta_63d=1.70,
        beta_252d=1.30,
        benchmark_return_21d=-0.01,
        benchmark_return_63d=-0.02,
        protocol=protocol,
    )
    assert fired is True
    assert multiplier == 0.75
    assert set(reasons) == {
        "HIGH_DOWNSIDE_BETA",
        "RELIABLE_MARKET_LINK",
        "BETA_ACCELERATING",
        "MARKET_21D_NEGATIVE",
        "MARKET_63D_NEGATIVE",
    }


def test_no_reduction_in_positive_market_even_with_high_beta():
    protocol = _protocol()
    multiplier, fired, _ = regime_multiplier(
        downside_beta_252d=1.70,
        r2_252d=0.70,
        beta_63d=1.90,
        beta_252d=1.40,
        benchmark_return_21d=0.01,
        benchmark_return_63d=0.02,
        protocol=protocol,
    )
    assert fired is False
    assert multiplier == 1.0


def test_no_reduction_when_beta_link_is_unreliable():
    protocol = _protocol()
    multiplier, fired, reasons = regime_multiplier(
        downside_beta_252d=1.70,
        r2_252d=0.20,
        beta_63d=1.90,
        beta_252d=1.40,
        benchmark_return_21d=-0.01,
        benchmark_return_63d=-0.02,
        protocol=protocol,
    )
    assert fired is False
    assert multiplier == 1.0
    assert "RELIABLE_MARKET_LINK" not in reasons


def test_missing_input_never_reduces_position():
    protocol = _protocol()
    multiplier, fired, reasons = regime_multiplier(
        downside_beta_252d=None,
        r2_252d=0.60,
        beta_63d=1.80,
        beta_252d=1.40,
        benchmark_return_21d=-0.01,
        benchmark_return_63d=-0.02,
        protocol=protocol,
    )
    assert fired is False
    assert multiplier == 1.0
    assert reasons == ["MISSING_TRIGGER_INPUT"]


def test_thresholds_match_existing_risk_engine_governance():
    root = Path(__file__).resolve().parents[1]
    regime = _protocol()["primary_intervention"]["trigger_all"]
    risk = json.loads((root / "config" / "BETA_CORRELATION_RISK_ENGINE.json").read_text(encoding="utf-8"))
    assert regime["downside_beta_252d_min"] == 1.30
    assert regime["r2_252d_min"] == risk["reliability"]["medium_min"]
    assert regime["beta_acceleration_63d_minus_252d_min"] == risk["reliability"]["nonstationarity_span_warning"]
    assert regime["benchmark_return_21d_max"] == 0.0
    assert regime["benchmark_return_63d_max"] == 0.0


def test_multiplier_does_not_change_trade_sign():
    returns = pd.Series([0.04, -0.08, 0.01])
    adjusted = returns * 0.75
    assert (returns > 0).tolist() == (adjusted > 0).tolist()
