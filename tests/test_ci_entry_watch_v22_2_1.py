from __future__ import annotations

import pandas as pd

from v182.reporting import ci_entry_watch_v22_2_1 as mod


def _cfg():
    return {
        "market_entry_gate": {
            "risk_off_blocks_entry_review": True,
            "cnn_extreme_greed_threshold": 75.0,
            "minimum_confidence_for_caution_review": 70.0,
            "action_market_scope": "EUROPE",
            "etf_default_market_scope": "GLOBAL",
        },
        "confidence_adjustment": {
            "risk_on_points": 5.0,
            "neutral_points": 0.0,
            "risk_off_points": -15.0,
            "extreme_greed_points": -5.0,
            "min": 0.0,
            "max": 100.0,
        },
        "potential": {"plausible_min_pct": -100.0, "plausible_max_pct": 300.0},
    }


def test_action_consensus_upside_has_priority():
    value, method, ref = mod._potential("ACTION", {"last_close": 100, "upside_pct": 18.0, "high_52w": 150}, _cfg())
    assert value == 18.0
    assert method == "CONSENSUS_UPSIDE"
    assert ref == 118.0


def test_etf_uses_52w_high_potential():
    value, method, ref = mod._potential("ETF", {"last_close": 80, "high_52w": 100}, _cfg())
    assert value == 25.0
    assert method == "TECHNICAL_TO_52W_HIGH"
    assert ref == 100.0


def test_risk_off_blocks_ready_entry_review():
    row = pd.Series({
        "asset_class": "ACTION",
        "CI_MARKET_ORIENTATION_EUROPE": "RISK_OFF",
        "CI_MARKET_CNN_FEAR_GREED": 45,
        "v22_2_entry_state": "READY_FOR_REVIEW",
    })
    gate, _, points, _ = mod._market_gate(row, {}, _cfg())
    state, reason = mod._final_entry_state(row, gate, 80.0 + points, _cfg())
    assert gate == "BLOCK"
    assert state == "WAIT"
    assert reason == "MARKET_ORIENTATION_BLOCK"


def test_risk_on_never_promotes_unconfirmed_technical_trigger():
    row = pd.Series({
        "asset_class": "ACTION",
        "CI_MARKET_ORIENTATION_EUROPE": "RISK_ON",
        "CI_MARKET_CNN_FEAR_GREED": 60,
        "v22_2_entry_state": "WAIT",
    })
    gate, _, points, _ = mod._market_gate(row, {}, _cfg())
    state, reason = mod._final_entry_state(row, gate, 65.0 + points, _cfg())
    assert gate == "PASS"
    assert state == "WAIT"
    assert reason == "BASE_TECHNICAL_TRIGGER_NOT_READY"


def test_extreme_greed_requires_minimum_confidence():
    row = pd.Series({
        "asset_class": "ACTION",
        "CI_MARKET_ORIENTATION_EUROPE": "RISK_ON",
        "CI_MARKET_CNN_FEAR_GREED": 80,
        "v22_2_entry_state": "READY_FOR_REVIEW",
    })
    gate, _, _, _ = mod._market_gate(row, {}, _cfg())
    state, _ = mod._final_entry_state(row, gate, 68.0, _cfg())
    assert gate == "CAUTION"
    assert state == "WAIT"


def test_selection_score_is_not_mutated_by_overlay_contract():
    row = pd.Series({"score": 91.56, "v22_2_entry_state": "WAIT"})
    state, _ = mod._final_entry_state(row, "PASS", 90.0, _cfg())
    assert row["score"] == 91.56
    assert state == "WAIT"
