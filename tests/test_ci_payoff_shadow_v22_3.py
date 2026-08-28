import json
from pathlib import Path

import pandas as pd

from v182.reporting.ci_payoff_shadow_v22_3 import attach_payoff_shadow, payoff_from_rr

CFG = json.loads(Path("config/CI_PAYOFF_SHADOW_V22_3.json").read_text(encoding="utf-8"))


def test_rr_breakpoints():
    assert payoff_from_rr(None, CFG) == (0.0, "RR_UNAVAILABLE")
    assert payoff_from_rr(1.2, CFG)[0] == 0.0
    assert payoff_from_rr(1.6, CFG)[0] == 40.0
    assert payoff_from_rr(2.5, CFG)[0] == 70.0
    assert payoff_from_rr(5.0, CFG)[0] == 100.0


def test_allianz_style_rr_is_shadow_selected_without_changing_official_gate():
    frame = pd.DataFrame(
        [
            {
                "name": "ALLIANZ SE-REG",
                "isin": "DE0008404005",
                "asset_class": "ACTION",
                "score": 80.8,
                "CI_CONFIDENCE_SCORE_V22_2_1": 52.77,
                "CI_SELECTION_GATE_STATUS_V4": "REJECTED",
                "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY": 5.0,
                "SIM_RELIABILITY": 91.1,
                "v22_2_component_entry_timing": 60.0,
                "v22_2_component_trend_momentum": 70.0,
                "v22_2_component_selection_coverage": 90.0,
                "v22_2_component_temporal_stability": 40.0,
                "v22_2_component_provenance_quality": 40.0,
            }
        ]
    )
    out = attach_payoff_shadow(frame, CFG)
    assert out.loc[0, "CI_PAYOFF_SCORE_V22_3"] == 100.0
    assert out.loc[0, "CI_SHADOW_DECISION_V22_3"] == "SHADOW_SELECTED_RR"
    assert out.loc[0, "CI_SELECTION_GATE_STATUS_V4"] == "REJECTED"
    assert out.loc[0, "CI_CONFIDENCE_SCORE_V22_2_1"] == 52.77
    assert bool(out.loc[0, "CI_V4_GATE_UNCHANGED"]) is True
