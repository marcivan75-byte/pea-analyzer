import json
from pathlib import Path

from v182.backtest.risk_beta_regime_v12_pit_oos import regime_v12_multiplier


def _protocol() -> dict:
    root = Path(__file__).resolve().parents[1]
    return json.loads(
        (root / "config" / "BETA_RISK_REGIME_PIT_OOS_PROTOCOL_V1_2.json").read_text(
            encoding="utf-8"
        )
    )


def test_v12_protocol_is_new_locked_hypothesis():
    protocol = _protocol()
    assert protocol["version"] == "BETA_RISK_REGIME_PIT_OOS_V1_2_2026_08_16"
    assert protocol["locked_before_results"] is True
    assert protocol["design_basis"] == "NO_PNL_PREVALENCE_AUDIT_V1_1"
    assert protocol["final_holdout_opened"] is False
    assert protocol["primary_intervention"]["r2_252d_role"] == "REPORT_ONLY_NOT_GATE"
    assert protocol["governance"]["no_parameter_optimization_after_results"] is True


def test_high_downside_plus_acceleration_triggers():
    multiplier, fired, reasons = regime_v12_multiplier(
        downside_beta_252d=1.50,
        beta_63d=1.70,
        beta_252d=1.30,
        benchmark_return_21d=0.01,
        benchmark_return_63d=0.02,
        protocol=_protocol(),
    )
    assert fired is True
    assert multiplier == 0.75
    assert "HIGH_DOWNSIDE_BETA" in reasons
    assert "BETA_ACCELERATING" in reasons


def test_high_downside_plus_negative_21d_market_triggers():
    multiplier, fired, reasons = regime_v12_multiplier(
        downside_beta_252d=1.40,
        beta_63d=1.20,
        beta_252d=1.10,
        benchmark_return_21d=-0.01,
        benchmark_return_63d=0.01,
        protocol=_protocol(),
    )
    assert fired is True
    assert multiplier == 0.75
    assert "MARKET_21D_NEGATIVE" in reasons


def test_high_downside_plus_negative_63d_market_triggers():
    multiplier, fired, reasons = regime_v12_multiplier(
        downside_beta_252d=1.40,
        beta_63d=1.20,
        beta_252d=1.10,
        benchmark_return_21d=0.01,
        benchmark_return_63d=-0.01,
        protocol=_protocol(),
    )
    assert fired is True
    assert multiplier == 0.75
    assert "MARKET_63D_NEGATIVE" in reasons


def test_high_downside_without_deterioration_does_not_trigger():
    multiplier, fired, _ = regime_v12_multiplier(
        downside_beta_252d=1.70,
        beta_63d=1.20,
        beta_252d=1.10,
        benchmark_return_21d=0.01,
        benchmark_return_63d=0.02,
        protocol=_protocol(),
    )
    assert fired is False
    assert multiplier == 1.0


def test_regime_deterioration_without_high_downside_does_not_trigger():
    multiplier, fired, reasons = regime_v12_multiplier(
        downside_beta_252d=1.10,
        beta_63d=1.60,
        beta_252d=1.10,
        benchmark_return_21d=-0.01,
        benchmark_return_63d=-0.02,
        protocol=_protocol(),
    )
    assert fired is False
    assert multiplier == 1.0
    assert "HIGH_DOWNSIDE_BETA" not in reasons


def test_missing_input_is_fail_safe_no_reduction():
    multiplier, fired, reasons = regime_v12_multiplier(
        downside_beta_252d=1.50,
        beta_63d=None,
        beta_252d=1.20,
        benchmark_return_21d=-0.01,
        benchmark_return_63d=-0.02,
        protocol=_protocol(),
    )
    assert fired is False
    assert multiplier == 1.0
    assert reasons == ["MISSING_TRIGGER_INPUT"]


def test_v12_keeps_same_magnitude_as_v11():
    protocol = _protocol()
    assert protocol["primary_intervention"]["triggered_position_multiplier"] == 0.75
    assert protocol["primary_intervention"]["otherwise_position_multiplier"] == 1.0
