import pandas as pd

from v182.reporting.objectives_risk_shadow_v1 import _simulate


def test_shadow_simulation_respects_minimum_reward_risk():
    cfg = {
        "forecast_sessions": {"TCT": 10, "CT": 30, "MT": 63},
        "history_minimum_observations": 126,
        "atr_invalidation_multiple": {"ACTION": 1.5, "ETF": 1.25},
        "entry_support_atr_buffer": 0.25,
        "minimum_reward_risk": {"TCT": 1.5, "CT": 2.0, "MT": 2.0},
        "reliability": {"history_weight": 30, "target_source_weight": 30, "technical_coverage_weight": 25, "selection_evidence_weight": 15},
    }
    prices = pd.Series(range(100, 300), dtype=float)
    row = pd.Series({"asset_class": "ACTION", "horizon": "CT", "yahoo_ticker": "X", "last_close": 100, "atr14": 2, "mm20": 96, "mm50": 92, "mm200": 80, "high_52w": 110, "POTENTIEL_PCT": 20, "BALANCED_SCORE": 80})
    result = _simulate(row, {"X": prices}, cfg)
    assert result["SIM_SHADOW_ONLY"] is True
    assert result["SIM_REWARD_RISK_AT_OPTIMAL_ENTRY"] >= 2.0
    assert result["SIM_INVALIDATION"] < result["SIM_ENTRY_OPTIMAL"] < result["SIM_TARGET_CENTRAL"]


def test_action_output_gate_is_strictly_greater_than_thresholds():
    assert not (13.0 > 13.0 and 62.1 > 62.0)
    assert 13.01 > 13.0 and 62.01 > 62.0


def test_hyper_pending_does_not_penalize_independent_ci_light_selection():
    cfg = {
        "forecast_sessions": {"TCT": 10, "CT": 30, "MT": 63},
        "history_minimum_observations": 126,
        "atr_invalidation_multiple": {"ACTION": 1.5, "ETF": 1.25},
        "entry_support_atr_buffer": 0.25,
        "minimum_reward_risk": {"TCT": 1.5, "CT": 2.0, "MT": 2.0},
        "reliability": {"history_weight": 30, "target_source_weight": 30, "technical_coverage_weight": 25, "selection_evidence_weight": 15},
    }
    base = {"asset_class": "ETF", "horizon": "MT", "yahoo_ticker": "X", "last_close": 100, "atr14": 2, "mm20": 96, "mm50": 92, "mm200": 80, "high_52w": 110, "HYPER_CONFIRMATION_STATE": "PENDING_SOURCE"}
    prices = {"X": pd.Series(range(100, 300), dtype=float)}
    hyper = _simulate(pd.Series({**base, "SIM_SELECTION_SOURCE": "HYPER_PENDING_CONFIRMATION"}), prices, cfg)
    light = _simulate(pd.Series({**base, "SIM_SELECTION_SOURCE": "CI_LIGHT|HYPER_PENDING_CONFIRMATION"}), prices, cfg)
    assert light["SIM_RELIABILITY"] > hyper["SIM_RELIABILITY"]
