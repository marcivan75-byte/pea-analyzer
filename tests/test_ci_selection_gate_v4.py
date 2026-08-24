from __future__ import annotations

import pandas as pd

from v182.reporting import ci_selection_gate_v4 as gate


SELECTION = {
    "minimum_selection_score": 77.0,
    "minimum_confidence_score": 66.0,
    "action_minimum_consensus_upside_pct": 20.0,
    "action_consensus_methods": ["CONSENSUS_UPSIDE"],
    "action_boursorama_positive": ["BUY", "STRONG_BUY"],
    "action_boursorama_hold": ["HOLD"],
    "action_boursorama_negative": ["SELL", "STRONG_SELL"],
    "etf_minimum_morningstar_stars": 3.0,
    "technical_entry_positive": ["BUY", "STRONG_BUY"],
    "technical_neutral": ["NEUTRAL"],
    "technical_exit_review": ["SELL", "STRONG_SELL"],
    "technical_horizon_mapping": {"TCT": "DAILY", "CT": "WEEKLY", "MT": "MONTHLY"},
}


def _row(**changes) -> pd.Series:
    data = {
        "asset_class": "ACTION",
        "horizon": "CT",
        "score": 77.0,
        "CI_CONFIDENCE_SCORE_V22_2_1": 66.0,
        "CI_POTENTIAL_METHOD": "CONSENSUS_UPSIDE",
        "CI_POTENTIAL_UPSIDE_PCT": 20.0,
        "boursorama_consensus": "BUY",
        "tradingview_daily_signal": "NEUTRAL",
        "tradingview_weekly_signal": "BUY",
        "tradingview_monthly_signal": "STRONG_BUY",
        "V22_2_1_ENTRY_STATE": "READY_FOR_REVIEW",
    }
    data.update(changes)
    return pd.Series(data)


def test_reference_thresholds_are_inclusive_and_technical_potential_is_not_consensus():
    assert gate._base_gate(_row(), SELECTION) == (True, [])
    passed, reasons = gate._base_gate(
        _row(CI_POTENTIAL_METHOD="TECHNICAL_TO_52W_HIGH", CI_POTENTIAL_UPSIDE_PCT=99), SELECTION
    )
    assert passed is False
    assert "ACTION_ANALYST_CONSENSUS_UPSIDE_MISSING" in reasons


def test_etf_uses_morningstar_and_never_equity_analyst_consensus():
    passed, reasons = gate._base_gate(
        _row(asset_class="ETF", morningstar_rating=3, CI_POTENTIAL_METHOD=None, CI_POTENTIAL_UPSIDE_PCT=None),
        SELECTION,
    )
    assert passed is True
    assert not any("CONSENSUS" in reason for reason in reasons)
    assert gate._boursorama_gate(_row(asset_class="ETF", boursorama_consensus=None), SELECTION)[0] == "PASS_CONTEXT_ONLY"
    assert gate._base_gate(_row(asset_class="ETF", morningstar_rating=2.99), SELECTION)[0] is False
    assert gate._base_gate(_row(asset_class="ETF", morningstar_rating=None), SELECTION)[0] is False


def test_tradingview_horizon_mapping_is_exact():
    expectations = {"TCT": "NEUTRAL", "CT": "BUY", "MT": "STRONG_BUY"}
    for horizon, expected in expectations.items():
        signal, _ = gate._tradingview_signal(_row(horizon=horizon), SELECTION)
        assert signal == expected


def test_tradingview_states_are_fail_closed_not_bearish_when_missing():
    entry, exit_state, reason, _ = gate._tradingview_gate(
        _row(tradingview_weekly_signal=pd.NA), SELECTION
    )
    assert entry == "WAIT_SOURCE_MISSING"
    assert exit_state == "NO_EXIT_SIGNAL"
    assert reason == "TRADINGVIEW_SIGNAL_MISSING"


def test_tradingview_sell_blocks_entry_and_requests_exit_review():
    entry, exit_state, _, _ = gate._tradingview_gate(_row(tradingview_weekly_signal="SELL"), SELECTION)
    assert entry == "BLOCK_ENTRY"
    assert exit_state == "EXIT_REVIEW_IF_HELD"


def test_source_confirmation_cannot_resurrect_failed_base_candidate():
    row = _row(score=10, tradingview_weekly_signal="STRONG_BUY")
    entry, _, reason = gate._effective_states(
        row,
        base_pass=False,
        boursorama_gate="PASS",
        technical_entry_gate="STRONG_CONFIRM",
        technical_exit_gate="NO_EXIT_SIGNAL",
    )
    assert entry == "REJECTED_BASE"
    assert reason == "BASE_SELECTION_GATE_FAILED"


def test_ready_requires_all_independent_gates():
    entry, exit_state, reason = gate._effective_states(
        _row(),
        base_pass=True,
        boursorama_gate="PASS",
        technical_entry_gate="ENTRY_CONFIRM",
        technical_exit_gate="NO_EXIT_SIGNAL",
    )
    assert (entry, exit_state, reason) == (
        "READY_FOR_REVIEW",
        "NO_EXIT_SIGNAL",
        "QUALITY_TRIGGER_AND_TRADINGVIEW_CONFIRMED",
    )
    assert gate._effective_states(
        _row(),
        base_pass=True,
        boursorama_gate="WAIT_SOURCE_MISSING",
        technical_entry_gate="ENTRY_CONFIRM",
        technical_exit_gate="NO_EXIT_SIGNAL",
    )[0] == "WAIT"


def test_master_context_adds_etf_rating_without_touching_action():
    rows = pd.DataFrame(
        [
            {"isin": "ETF1", "asset_class": "ETF"},
            {"isin": "ACT1", "asset_class": "ACTION"},
        ]
    )
    etfs = pd.DataFrame([{"isin": "ETF1", "morningstar_rating": 4}])
    result = gate._attach_master_context(rows, pd.DataFrame(), etfs)
    assert float(result.loc[result["isin"].eq("ETF1"), "morningstar_rating"].iloc[0]) == 4
    assert pd.isna(result.loc[result["isin"].eq("ACT1"), "morningstar_rating"].iloc[0])
