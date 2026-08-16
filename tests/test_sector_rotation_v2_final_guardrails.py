from pathlib import Path
import json

import pandas as pd

from v182.features.sector_rotation_v2_final import (
    _reentry_final,
    _state_and_warnings_final,
    build_sector_rotation_v2,
)


ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "SECTOR_ROTATION_V2_SHADOW.json").read_text(encoding="utf-8"))


def test_missing_rls_families_do_not_count_as_present_neutral_evidence():
    rows = []
    for sector, base in (("A", 10.0), ("B", 5.0), ("C", -2.0)):
        for index in range(5):
            rows.append({"isin": f"{sector}{index}", "sector": sector, "perf_1m_pct": base + index * 0.1})
    result = build_sector_rotation_v2(pd.DataFrame(rows), CFG, as_of="2026-08-16")
    assert not result.sectors.empty
    # With only 1M performance available, the valid RLS families are relative strength,
    # early price and internal diffusion: 15% + 5% + 5% = at most 25% coverage.
    assert result.sectors["RLS_coverage"].max() <= 25.0001
    assert (result.sectors["DQS"] < CFG["governance"]["minimum_dqs_for_decision"]).all()


def _risk_row():
    return {
        "RLS": 85.0,
        "SQS": 75.0,
        "MCS": 75.0,
        "AVCR": 85.0,
        "DQS": 90.0,
        "valuation_justification": 50.0,
        "technical_overextension": 95.0,
        "crowding": 95.0,
        "price_fundamental_gap": 90.0,
        "breadth_score": 75.0,
        "sector_flow_score": None,
        "volatility_risk": 50.0,
    }


def test_static_overvaluation_alone_does_not_trigger_correction_alert():
    state = _state_and_warnings_final(_risk_row(), {"prior": None, "prior_velocity": None, "days_in_state": 0}, CFG)
    assert "PROMISING_BUT_OVERVALUED" in state["warnings"]
    assert "TECHNICAL_OVEREXTENSION" in state["warnings"]
    assert "CROWDING_EUPHORIA" in state["warnings"]
    assert state["dynamic_correction_confirmation"] is False
    assert state["correction_alert"] is False
    assert state["new_position_action"] == "NO_CHASE"


def test_correction_alert_requires_and_accepts_dynamic_deterioration():
    prior = pd.Series(
        {
            "RLS": 100.0,
            "SQS": 88.0,
            "breadth_score": 75.0,
            "RLS_velocity": 0.0,
            "state": "LEADERSHIP",
            "correction_alert": False,
            "warnings": "[]",
        }
    )
    context = {"prior": prior, "prior_velocity": 0.0, "days_in_state": 30}
    state = _state_and_warnings_final(_risk_row(), context, CFG)
    assert state["dynamic_correction_confirmation"] is True
    assert state["correction_alert"] is True
    assert "CORRECTION_ALERT" in state["warnings"]
    assert state["new_position_action"] == "NO_NEW_ENTRY"
    assert state["existing_position_action"] == "EXIT_REVIEW"


def test_zero_risk_subscore_is_not_mistaken_for_missing_in_reentry():
    prior = pd.Series({"correction_alert": True, "warnings": "['CORRECTION_ALERT']"})
    row = {
        "AVCR": 50.0,
        "technical_overextension": 0.0,
        "SQS": 80.0,
        "sector_flow_score": 50.0,
        "MCS": 70.0,
        "volatility_risk": 0.0,
    }
    readiness, state = _reentry_final(row, 0.0, prior, False, CFG)
    assert readiness > 70.0
    assert state == "REENTRY_FORMING"
