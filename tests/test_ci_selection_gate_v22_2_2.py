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
    "boursorama_action_gate": {
        "enabled": True,
        "accepted_consensus": ["BUY", "STRONG_BUY"],
        "wait_consensus": ["HOLD"],
        "rejected_consensus": ["SELL", "STRONG_SELL"],
        "missing_policy": "REVIEW_NOT_ENTRY_READY",
    },
    "investing_timing_gate": {
        "enabled": True,
        "entry_confirm": ["BUY", "STRONG_BUY"],
        "neutral": ["NEUTRAL"],
        "exit_review": ["SELL", "STRONG_SELL"],
        "missing_entry_policy": "WAIT_SOURCE_MISSING",
    },
}


def _row(**changes):
    base = {
        "isin": "FR0000000001",
        "asset_class": "ACTION",
        "horizon": "CT",
        "score": 77.0,
        "decision": "BUY_CANDIDATE",
        "CI_CONFIDENCE_SCORE_V22_2_1": 66.0,
        "CI_POTENTIAL_UPSIDE_PCT": 20.0,
        "CI_POTENTIAL_METHOD": "CONSENSUS_UPSIDE",
        "V22_2_1_ENTRY_STATE": "READY_FOR_REVIEW",
        "boursorama_consensus": "BUY",
        "investing_horizon_signal": "BUY",
        "investing_horizon_score": 1,
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


def test_boursorama_buy_and_strong_buy_pass_action_quality_gate():
    for consensus in ("BUY", "STRONG_BUY"):
        status, reason = gate._boursorama_gate(_row(boursorama_consensus=consensus), CFG)
        assert status == "PASS"
        assert consensus in reason


def test_boursorama_hold_waits_and_sell_states_reject():
    status, _ = gate._boursorama_gate(_row(boursorama_consensus="HOLD"), CFG)
    assert status == "WAIT"
    for consensus in ("SELL", "STRONG_SELL"):
        status, _ = gate._boursorama_gate(_row(boursorama_consensus=consensus), CFG)
        assert status == "REJECT"


def test_missing_boursorama_is_review_not_bearish():
    status, reason = gate._boursorama_gate(_row(boursorama_consensus=None), CFG)
    assert status == "REVIEW_SOURCE_MISSING"
    assert reason == "BOURSORAMA_CONSENSUS_MISSING"


def test_etf_does_not_require_boursorama_action_consensus():
    status, reason = gate._boursorama_gate(_row(asset_class="ETF", boursorama_consensus=None), CFG)
    assert status == "PASS_CONTEXT_ONLY"
    assert "NOT_APPLICABLE" in reason


def test_investing_buy_and_strong_buy_confirm_entry():
    expected = {"BUY": "ENTRY_CONFIRM", "STRONG_BUY": "STRONG_CONFIRM"}
    for signal, entry_gate in expected.items():
        entry, exit_gate, _, _ = gate._investing_gate(_row(investing_horizon_signal=signal), CFG)
        assert entry == entry_gate
        assert exit_gate == "NO_EXIT_SIGNAL"


def test_investing_neutral_waits():
    entry, exit_gate, _, _ = gate._investing_gate(_row(investing_horizon_signal="NEUTRAL"), CFG)
    assert entry == "WAIT_NO_NEW_ENTRY"
    assert exit_gate == "NO_EXIT_SIGNAL"


def test_investing_sell_blocks_entry_and_requests_exit_review():
    entry, exit_gate, _, _ = gate._investing_gate(_row(investing_horizon_signal="SELL"), CFG)
    assert entry == "BLOCK_ENTRY"
    assert exit_gate == "EXIT_REVIEW_IF_HELD"
    entry, exit_gate, _, _ = gate._investing_gate(_row(investing_horizon_signal="STRONG_SELL"), CFG)
    assert entry == "BLOCK_ENTRY"
    assert exit_gate == "STRONG_EXIT_REVIEW_IF_HELD"


def test_missing_investing_waits_and_is_not_exit_signal():
    entry, exit_gate, reason, _ = gate._investing_gate(
        _row(investing_horizon_signal=None, investing_weekly_signal=None), CFG
    )
    assert entry == "WAIT_SOURCE_MISSING"
    assert exit_gate == "NO_EXIT_SIGNAL"
    assert reason == "INVESTING_SIGNAL_MISSING"


def test_investing_cannot_create_candidate_when_base_gate_fails():
    row = _row(score=60.0, investing_horizon_signal="STRONG_BUY")
    base_pass, _ = gate._gate_row(row, CFG)
    b_gate, _ = gate._boursorama_gate(row, CFG)
    i_entry, i_exit, _, _ = gate._investing_gate(row, CFG)
    entry, _, reason = gate._effective_states(
        row,
        base_pass=base_pass,
        boursorama_gate=b_gate,
        investing_entry_gate=i_entry,
        investing_exit_gate=i_exit,
    )
    assert entry == "REJECTED_BASE"
    assert reason == "BASE_SELECTION_GATE_FAILED"


def test_ready_requires_upstream_trigger_and_investing_confirmation():
    row = _row()
    entry, exit_state, reason = gate._effective_states(
        row,
        base_pass=True,
        boursorama_gate="PASS",
        investing_entry_gate="ENTRY_CONFIRM",
        investing_exit_gate="NO_EXIT_SIGNAL",
    )
    assert entry == "READY_FOR_REVIEW"
    assert exit_state == "NO_EXIT_SIGNAL"
    assert reason == "QUALITY_TRIGGER_AND_INVESTING_CONFIRMED"

    waiting = _row(V22_2_1_ENTRY_STATE="WAIT", investing_horizon_signal="STRONG_BUY")
    entry, _, reason = gate._effective_states(
        waiting,
        base_pass=True,
        boursorama_gate="PASS",
        investing_entry_gate="STRONG_CONFIRM",
        investing_exit_gate="NO_EXIT_SIGNAL",
    )
    assert entry == "WAIT"
    assert reason == "UPSTREAM_TECHNICAL_OR_MARKET_TRIGGER_NOT_READY"


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
