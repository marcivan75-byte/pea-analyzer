from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.risk.entry_exit_governance_v21_8 import apply_governance, classify_entry

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8"))


def _ready_ct(**overrides):
    row = {
        "asset_class": "ACTION",
        "horizon": "CT",
        "decision": "BUY_CANDIDATE",
        "score": 88.0,
        "source_validation_state": "FULLY_VALIDATED",
        "investing_required_timeframe": "WEEKLY",
        "investing_horizon_signal": "STRONG_BUY",
        "dist_sma50": -0.01,
        "dist_sma200": 0.10,
        "momentum_accel": 0.02,
        "vol20": 0.20,
    }
    row.update(overrides)
    return row


def test_timing_wait_blocks_entry_before_other_timing_rules():
    row = _ready_ct(
        source_validation_state="TIMING_WAIT",
        investing_horizon_signal="BUY",
    )
    state, reasons = classify_entry(pd.Series(row), CFG)
    assert state == "WAIT"
    assert reasons == ["INVESTING_WEEKLY_BUY_NOT_STRONG_BUY"]


def test_missing_boursorama_priority_context_blocks_entry():
    state, reasons = classify_entry(pd.Series(_ready_ct(source_validation_state="BOURSORAMA_INCOMPLETE")), CFG)
    assert state == "WAIT"
    assert reasons == ["BOURSORAMA_PRIORITY_CONTEXT_INCOMPLETE"]


def test_fully_validated_ct_can_reach_action_when_existing_timing_evidence_passes():
    state, reasons = classify_entry(pd.Series(_ready_ct()), CFG)
    assert state == "ACTION"
    assert "SOURCE_CONFIRMATION_REQUIRED_BEFORE_ENTRY" not in reasons


def test_fully_validated_tct_still_requires_exact_t2():
    row = _ready_ct(
        horizon="TCT",
        source_validation_state="FULLY_VALIDATED",
        investing_required_timeframe="DAILY",
        tct_setup="T1",
    )
    state, reasons = classify_entry(pd.Series(row), CFG)
    assert state == "WAIT"
    assert reasons == ["TCT_EXACT_T2_CONFIRMATION_REQUIRED"]
    row["tct_setup"] = "T2_EXACT_TIMING_CONFIRMATION"
    state, _ = classify_entry(pd.Series(row), CFG)
    assert state == "ACTION"


def test_source_gate_can_only_change_entry_readiness_not_score_or_decision():
    frame = pd.DataFrame([
        _ready_ct(source_validation_state="TIMING_WAIT", investing_horizon_signal="NEUTRAL"),
        _ready_ct(source_validation_state="FULLY_VALIDATED"),
    ])
    before = frame[["decision", "score"]].copy()
    governed = apply_governance(frame, CFG)
    assert governed[["decision", "score"]].equals(before)
    assert list(governed["v21_8_entry_state"]) == ["WAIT", "ACTION"]
    assert (governed["v21_8_score_influence"] == 0.0).all()
    assert (governed["v21_8_decision_influence"] == 0.0).all()
