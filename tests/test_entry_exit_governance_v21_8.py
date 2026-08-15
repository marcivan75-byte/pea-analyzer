import json
from pathlib import Path
import pandas as pd

from v182.risk.entry_exit_governance_v21_8 import apply_governance, classify_entry, classify_position

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "V21_8_ENTRY_EXIT_GOVERNANCE.json").read_text(encoding="utf-8"))


def test_extreme_score_requires_wait_not_penalty():
    row = pd.Series({"decision": "ACTION", "score_final": 94.0, "dist_sma50": 0.05, "dist_sma200": 0.2, "momentum_accel": 0.01, "vol20": 0.2})
    state, reasons = classify_entry(row, CFG)
    assert state == "WAIT"
    assert "EXTREME_SCORE_REQUIRES_OVEREXTENSION_REVIEW" in reasons


def test_missing_timing_evidence_never_fabricates_action():
    state, reasons = classify_entry(pd.Series({"decision": "ACTION", "score_final": 88.0}), CFG)
    assert state == "WAIT"
    assert reasons == ["ENTRY_TIMING_EVIDENCE_MISSING"]


def test_no_fixed_take_profit_or_new_hard_stop():
    df = pd.DataFrame([{"decision": "ACTION", "score_final": 88.0, "dist_sma50": 0.01, "dist_sma200": 0.1, "momentum_accel": 0.01, "vol20": 0.2, "return_since_entry": 0.25}])
    out = apply_governance(df, CFG)
    assert bool(out.loc[0, "v21_8_fixed_take_profit"]) is False
    assert bool(out.loc[0, "v21_8_new_hard_stop_promoted"]) is False
    assert out.loc[0, "v21_8_position_state"] == "HOLD"


def test_combined_trend_and_momentum_deterioration_is_exit_candidate():
    state, reasons = classify_position(pd.Series({"dist_sma200": -0.01, "momentum_accel": -0.02}), CFG)
    assert state == "EXIT"
    assert len(reasons) == 2
