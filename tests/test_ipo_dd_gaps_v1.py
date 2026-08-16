from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.decision.ipo_dd_gaps_v1 import build_gap_worklist

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))


def _complete_row(config: dict) -> dict:
    row = {
        "candidate_id": "NASDAQ:TEST",
        "identity_key": "NAME:TEST",
        "name": "Test IPO",
        "symbol": "TEST",
        "exchange": "NASDAQ",
        "expected_date": "2026-09-10",
        "decision": "WATCH",
        "live_order_allowed": False,
    }
    for criterion in config["opportunity_weights"]:
        row[f"opportunity_{criterion}"] = 80
    for criterion in config["risk_weights"]:
        row[f"risk_{criterion}"] = 30
    return row


def test_gap_worklist_prioritizes_effective_final_score_weight() -> None:
    config = _config()
    row = _complete_row(config)
    row["decision"] = "WATCH_DATA_GAP"
    row["opportunity_revenue_growth"] = None
    row["opportunity_valuation_vs_peers"] = None
    row["risk_loss_cash_burn"] = None
    row["risk_valuation"] = None
    frame = build_gap_worklist(pd.DataFrame([row]), config)
    result = frame.iloc[0]
    assert result["dd_status"] == "ACTION_REQUIRED"
    assert result["missing_criteria_count"] == 4
    # 14*60% + 14*60% + 14*40% + 13*40% = 27.6% of the final IPO score.
    assert result["missing_weight_total_pct"] == 27.6
    assert result["effective_covered_weight_pct"] == 72.4
    assert result["priority_1_weight_pct"] == 8.4
    assert result["priority_1_dimension_weight_pct"] == 14.0
    assert result["priority_1_criterion"] in {"revenue_growth", "valuation_vs_peers"}
    assert isinstance(result["priority_1_action"], str) and result["priority_1_action"]
    assert result["live_order_allowed"] == False


def test_derived_scoring_signals_are_not_falsely_reported_missing() -> None:
    config = _config()
    row = _complete_row(config)
    row["opportunity_bookbuilding_demand"] = None
    row["bookbuilding_demand"] = 65
    row["opportunity_float_liquidity"] = None
    row["float_liquidity"] = 78
    row["risk_small_float_liquidity"] = None
    row["small_float_liquidity"] = 22
    row["risk_deal_instability"] = None
    row["deal_instability"] = 20
    frame = build_gap_worklist(pd.DataFrame([row]), config)
    result = frame.iloc[0]
    assert result["dd_status"] == "COMPLETE"
    assert result["missing_criteria_count"] == 0
    assert result["missing_weight_total_pct"] == 0.0


def test_identity_conflict_is_an_explicit_blocking_due_diligence_action() -> None:
    config = _config()
    row = _complete_row(config)
    row["identity_name_conflict"] = True
    row["decision"] = "WATCH_IDENTITY_CONFLICT"
    frame = build_gap_worklist(pd.DataFrame([row]), config)
    result = frame.iloc[0]
    assert result["dd_status"] == "ACTION_REQUIRED"
    assert result["blocking_issue"] == "IDENTITY_CONFLICT"
    assert "Réconcilier l'identité" in result["blocking_action"]
    assert result["missing_weight_total_pct"] == 0.0


def test_complete_candidate_has_no_due_diligence_gap() -> None:
    config = _config()
    row = _complete_row(config)
    frame = build_gap_worklist(pd.DataFrame([row]), config)
    assert frame.loc[0, "dd_status"] == "COMPLETE"
    assert frame.loc[0, "missing_criteria_count"] == 0
    assert frame.loc[0, "missing_weight_total_pct"] == 0.0
    assert frame.loc[0, "effective_covered_weight_pct"] == 100.0
    assert frame.loc[0, "all_required_actions"] == ""
