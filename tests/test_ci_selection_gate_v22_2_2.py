from __future__ import annotations

import pandas as pd

from v182.reporting import ci_selection_gate_v22_2_2 as gate


CFG = {
    "selection_gate": {
        "minimum_selection_score": 77.0,
        "minimum_confidence_score": 66.0,
        "action_minimum_analyst_consensus_upside_pct": 20.0,
    },
    "action_consensus_methods": [
        "CONSENSUS_UPSIDE",
        "YAHOO_CONSENSUS_UPSIDE",
        "CONSENSUS_TARGET_PRICE",
        "YAHOO_TARGET_MEAN",
    ],
}


def _row(**changes):
    base = {
        "isin": "FR0000000001",
        "asset_class": "ACTION",
        "score": 77.0,
        "CI_CONFIDENCE_SCORE_V22_2_1": 66.0,
        "CI_POTENTIAL_UPSIDE_PCT": 20.0,
        "CI_POTENTIAL_METHOD": "CONSENSUS_UPSIDE",
    }
    base.update(changes)
    return pd.Series(base)


def test_thresholds_are_inclusive():
    passed, reasons = gate._gate_row(_row(), CFG)
    assert passed is True
    assert reasons == []


def test_action_consensus_below_20_is_rejected():
    passed, reasons = gate._gate_row(_row(CI_POTENTIAL_UPSIDE_PCT=19.99), CFG)
    assert passed is False
    assert "ACTION_ANALYST_CONSENSUS_UPSIDE_LT_20" in reasons


def test_action_technical_52w_potential_cannot_satisfy_consensus_gate():
    passed, reasons = gate._gate_row(
        _row(CI_POTENTIAL_UPSIDE_PCT=45.0, CI_POTENTIAL_METHOD="TECHNICAL_TO_52W_HIGH"), CFG
    )
    assert passed is False
    assert "ACTION_ANALYST_CONSENSUS_UPSIDE_MISSING" in reasons


def test_etf_is_exempt_from_analyst_consensus_gate():
    passed, reasons = gate._gate_row(
        _row(asset_class="ETF", CI_POTENTIAL_UPSIDE_PCT=None, CI_POTENTIAL_METHOD="UNAVAILABLE"), CFG
    )
    assert passed is True
    assert not any("CONSENSUS" in reason for reason in reasons)


def test_selection_score_below_77_rejected_for_actions_and_etfs():
    for asset in ("ACTION", "ETF"):
        passed, reasons = gate._gate_row(_row(asset_class=asset, score=76.99), CFG)
        assert passed is False
        assert "SELECTION_SCORE_LT_77" in reasons


def test_confidence_below_66_rejected_for_actions_and_etfs():
    for asset in ("ACTION", "ETF"):
        passed, reasons = gate._gate_row(
            _row(asset_class=asset, CI_CONFIDENCE_SCORE_V22_2_1=65.99), CFG
        )
        assert passed is False
        assert "CONFIDENCE_SCORE_LT_66" in reasons


def test_boursorama_direct_urls_are_asset_specific():
    action = pd.Series({"isin": "FR0000120271", "asset_class": "ACTION", "yahoo_ticker": "TTE.PA"})
    action_url, action_status = gate._boursorama_link("ACTION", action, {})
    assert action_url == "https://www.boursorama.com/cours/consensus/1rPTTE/"
    assert action_status == "DIRECT_DETERMINISTIC_CONSENSUS"

    etf = pd.Series({"isin": "LU0000000001", "asset_class": "ETF", "yahoo_ticker": "TEST.PA"})
    etf_url, etf_status = gate._boursorama_link("ETF", etf, {})
    assert etf_url == "https://www.boursorama.com/bourse/trackers/cours/1rTTEST/"
    assert etf_status == "DIRECT_DETERMINISTIC"


def test_investing_prefers_validated_isin_map_and_has_search_fallback():
    row = pd.Series({"isin": "FR0000000001", "name": "Example"})
    direct, status = gate._investing_link(
        "FR0000000001", row, {}, {"FR0000000001": "https://www.investing.com/equities/example"}
    )
    assert direct == "https://www.investing.com/equities/example"
    assert status == "VALIDATED_ISIN_MAP"

    fallback, fallback_status = gate._investing_link("FR0000000002", row, {}, {})
    assert fallback.startswith("https://www.investing.com/search/?q=")
    assert fallback_status == "SEARCH_FALLBACK"
