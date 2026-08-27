from __future__ import annotations

import pandas as pd

from v182.reporting import ci_selection_gate_v22_2_2 as base
from v182.reporting import ci_selection_gate_v22_2_2_etf_morningstar as gate


CFG = {
    "selection_gate": {
        "minimum_selection_score": 77.0,
        "minimum_confidence_score": 66.0,
        "action_minimum_analyst_consensus_upside_pct": 20.0,
        "etf_minimum_morningstar_stars": 3.0,
    },
    "action_consensus_methods": ["CONSENSUS_UPSIDE"],
    "etf_morningstar_gate": {
        "enabled": True,
        "field": "morningstar_rating",
        "minimum_stars": 3.0,
        "missing_policy": "EXCLUDE_FAIL_CLOSED",
    },
}


def _etf(**changes):
    row = {
        "asset_class": "ETF",
        "isin": "LU0000000001",
        "score": 82.0,
        "CI_CONFIDENCE_SCORE_V22_2_1": 75.0,
        "CI_POTENTIAL_UPSIDE_PCT": None,
        "CI_POTENTIAL_METHOD": "UNAVAILABLE",
        "morningstar_rating": 3.0,
    }
    row.update(changes)
    return pd.Series(row)


def test_etf_three_stars_is_inclusive_and_needs_no_analyst_consensus():
    passed, reasons = gate._gate_row_with_etf_morningstar(_etf(), CFG, base._gate_row)
    assert passed is True
    assert reasons == []


def test_etf_five_stars_passes():
    passed, reasons = gate._gate_row_with_etf_morningstar(
        _etf(morningstar_rating=5.0), CFG, base._gate_row
    )
    assert passed is True
    assert reasons == []


def test_etf_below_three_stars_is_rejected():
    passed, reasons = gate._gate_row_with_etf_morningstar(
        _etf(morningstar_rating=2.0), CFG, base._gate_row
    )
    assert passed is False
    assert "ETF_MORNINGSTAR_RATING_LT_3" in reasons


def test_etf_missing_rating_is_fail_closed():
    passed, reasons = gate._gate_row_with_etf_morningstar(
        _etf(morningstar_rating=None), CFG, base._gate_row
    )
    assert passed is False
    assert "ETF_MORNINGSTAR_RATING_MISSING" in reasons


def test_attach_master_identity_adds_etf_morningstar_without_touching_actions():
    selected = pd.DataFrame([
        {"isin": "ETF1", "asset_class": "ETF", "score": 82.0},
        {"isin": "ACT1", "asset_class": "ACTION", "score": 82.0},
    ])
    actions = pd.DataFrame([{"isin": "ACT1", "name": "Action"}])
    etfs = pd.DataFrame([{"isin": "ETF1", "name": "ETF", "morningstar_rating": 4.0}])
    result = gate._attach_master_identity_with_morningstar(
        selected, actions, etfs, base.selected_source_enrichment.attach_master_identity
    )
    etf_rating = result.loc[result["isin"].eq("ETF1"), "morningstar_rating"].iloc[0]
    action_rating = result.loc[result["isin"].eq("ACT1"), "morningstar_rating"].iloc[0]
    assert float(etf_rating) == 4.0
    assert pd.isna(action_rating)
