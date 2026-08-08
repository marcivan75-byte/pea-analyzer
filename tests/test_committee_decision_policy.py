import pandas as pd

from v182.decision.committee_decision_policy import classify_action


def _row(**kwargs):
    base = {
        "comite_status": "RESEARCH_ONLY",
        "committee_score_with_analyst_momentum": 75.0,
        "target_upside_pct": 20.0,
        "consensus_score_100": 75.0,
        "committee_analyst_gate": "NEUTRAL",
    }
    base.update(kwargs)
    return pd.Series(base)


def test_buy_candidate_requires_three_confirmations():
    decision, execution, _ = classify_action(_row())
    assert decision == "BUY_CANDIDATE"
    assert execution == "RECOMMENDATION_ONLY"


def test_blocked_status_stays_blocked():
    decision, execution, _ = classify_action(_row(comite_status="BLOCKED"))
    assert decision == "NONE"
    assert execution == "BLOCKED"


def test_negative_analyst_gate_prevents_buy_candidate():
    decision, execution, _ = classify_action(_row(committee_analyst_gate="BLOCK_NEW_BUY_REVIEW"))
    assert decision == "REVIEW"
    assert execution == "RESEARCH_ONLY"


def test_watch_for_partial_confirmation():
    decision, execution, _ = classify_action(
        _row(committee_score_with_analyst_momentum=72.5, target_upside_pct=11.0, consensus_score_100=55.0)
    )
    assert decision == "WATCH"
    assert execution == "RECOMMENDATION_ONLY"
