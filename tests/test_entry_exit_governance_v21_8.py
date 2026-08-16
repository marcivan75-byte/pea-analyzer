import json
from pathlib import Path
import pandas as pd

from v182.risk.entry_exit_governance_v21_8 import apply_governance, classify_entry, classify_position

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8"))


def test_extreme_score_requires_wait_not_penalty():
    row = pd.Series({"decision": "ACTION", "horizon": "MT", "score_final": 94.0, "dist_sma50": 0.05, "dist_sma200": 0.2, "momentum_accel": 0.01, "vol20": 0.2})
    state, reasons = classify_entry(row, CFG)
    assert state == "WAIT"
    assert "EXTREME_SCORE_REQUIRES_OVEREXTENSION_REVIEW" in reasons


def test_missing_timing_evidence_never_fabricates_action():
    state, reasons = classify_entry(pd.Series({"decision": "ACTION", "horizon": "CT", "score_final": 88.0}), CFG)
    assert state == "WAIT"
    assert reasons == ["ENTRY_TIMING_EVIDENCE_MISSING"]


def test_tct_requires_exact_t2_confirmation():
    base = {"decision": "ACTION", "horizon": "TCT", "score_final": 88.0, "dist_sma50": -0.01, "dist_sma200": 0.10, "momentum_accel": 0.02, "vol20": 0.2}
    state, reasons = classify_entry(pd.Series(base), CFG)
    assert state == "WAIT"
    assert reasons == ["TCT_EXACT_T2_CONFIRMATION_REQUIRED"]
    base["tct_setup"] = "T2_EXACT_TIMING_CONFIRMATION"
    state, _ = classify_entry(pd.Series(base), CFG)
    assert state == "ACTION"


def test_no_fixed_take_profit_or_new_hard_stop():
    df = pd.DataFrame([{"decision": "ACTION", "horizon": "MT", "score_final": 88.0, "dist_sma50": 0.01, "dist_sma200": 0.1, "momentum_accel": 0.01, "vol20": 0.2, "return_since_entry": 0.25, "max_return_since_entry": 0.40}])
    out = apply_governance(df, CFG)
    assert bool(out.loc[0, "v21_8_fixed_take_profit"]) is False
    assert bool(out.loc[0, "v21_8_legacy_fixed_stop_engine"]) is False
    assert bool(out.loc[0, "v21_8_new_hard_stop_promoted"]) is False
    assert out.loc[0, "v21_8_position_state"] == "HOLD"


def test_multifactor_deterioration_first_moves_to_protect():
    row = pd.Series({"dist_sma50": -0.02, "dist_sma200": -0.01, "slope_sma50_20d": -0.02, "ret_21d": -0.05})
    state, reasons = classify_position(row, CFG)
    assert state == "PROTECT"
    assert "AWAIT_TEMPORAL_CONFIRMATION" in reasons


def test_multifactor_deterioration_exits_only_after_protect_confirmation():
    row = pd.Series({"dist_sma50": -0.02, "dist_sma200": -0.01, "slope_sma50_20d": -0.02, "ret_21d": -0.05, "previous_v21_8_position_state": "PROTECT"})
    state, reasons = classify_position(row, CFG)
    assert state == "EXIT"
    assert "MULTIFACTOR_DETERIORATION_CONFIRMED_AFTER_PROTECT" in reasons


def test_profit_giveback_alone_never_exits():
    row = pd.Series({"return_since_entry": 0.12, "max_return_since_entry": 0.28})
    state, reasons = classify_position(row, CFG)
    assert state == "HOLD"
    assert reasons == ["POSITIVE_POSITION_NO_VALIDATED_EXIT_TRIGGER"]


def test_emergency_exit_requires_explicit_flag():
    state, reasons = classify_position(pd.Series({"emergency_risk_flag": True}), CFG)
    assert state == "EMERGENCY_EXIT"
    assert reasons == ["EXPLICIT_EMERGENCY_RISK_FLAG"]
