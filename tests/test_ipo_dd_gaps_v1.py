from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.decision.ipo_dd_gaps_v1 import build_gap_worklist

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))


def test_gap_worklist_prioritizes_high_weight_missing_criteria() -> None:
    config = _config()
    row = {
        "candidate_id": "NASDAQ:TEST",
        "identity_key": "NAME:TEST",
        "name": "Test IPO",
        "symbol": "TEST",
        "exchange": "NASDAQ",
        "expected_date": "2026-09-10",
        "decision": "WATCH_DATA_GAP",
        "live_order_allowed": False,
    }
    for criterion in config["opportunity_weights"]:
        row[f"opportunity_{criterion}"] = 80
    for criterion in config["risk_weights"]:
        row[f"risk_{criterion}"] = 30
    row["opportunity_revenue_growth"] = None
    row["opportunity_valuation_vs_peers"] = None
    row["risk_loss_cash_burn"] = None
    row["risk_valuation"] = None
    frame = build_gap_worklist(pd.DataFrame([row]), config)
    assert len(frame) == 1
    result = frame.iloc[0]
    assert result["dd_status"] == "ACTION_REQUIRED"
    assert result["missing_criteria_count"] == 4
    assert result["missing_weight_total_pct"] == 55.0
    assert result["priority_1_weight_pct"] == 14.0
    assert result["priority_1_criterion"] in {"revenue_growth", "valuation_vs_peers", "loss_cash_burn"}
    assert isinstance(result["priority_1_action"], str) and result["priority_1_action"]
    assert "VALUATION" in {result["priority_1_category"], result["priority_2_category"], result["priority_3_category"]}
    assert result["live_order_allowed"] == False


def test_complete_candidate_has_no_due_diligence_gap() -> None:
    config = _config()
    row = {"candidate_id": "X:OK", "identity_key": "NAME:OK", "name": "Complete IPO", "decision": "WATCH", "live_order_allowed": False}
    for criterion in config["opportunity_weights"]:
        row[f"opportunity_{criterion}"] = 75
    for criterion in config["risk_weights"]:
        row[f"risk_{criterion}"] = 35
    frame = build_gap_worklist(pd.DataFrame([row]), config)
    assert frame.loc[0, "dd_status"] == "COMPLETE"
    assert frame.loc[0, "missing_criteria_count"] == 0
    assert frame.loc[0, "missing_weight_total_pct"] == 0.0
    assert frame.loc[0, "all_required_actions"] == ""
