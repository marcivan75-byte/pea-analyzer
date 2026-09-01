import pandas as pd

from v182.backtest.hebdo_meta_consensus_gate_audit73_v4 import StudyConfig, run_study


def ledger():
    return pd.DataFrame([
        {"symbol":"AAA","decision_at":"2026-08-24T08:00:00Z","entry_price":100.0,"return_pct":10.0,"exit_category":"D_REVERSAL"},
        {"symbol":"BBB","decision_at":"2026-08-24T08:00:00Z","entry_price":100.0,"return_pct":-9.0,"exit_category":"PROTECTIVE_STOP"},
        {"symbol":"CCC","decision_at":"2026-08-24T08:00:00Z","entry_price":100.0,"return_pct":25.0,"exit_category":"D_REVERSAL"},
    ])


def observations():
    return pd.DataFrame([
        {"symbol":"AAA","available_at":"2026-08-23T18:00:00Z","target_median":125.0,"consensus":"BUY","consensus_delta_4w":2.0,"n_analysts":12,"period_kind":"CURRENT"},
        {"symbol":"BBB","available_at":"2026-08-23T18:00:00Z","target_median":130.0,"consensus":"BUY","consensus_delta_4w":1.0,"n_analysts":4,"period_kind":"CURRENT"},
        {"symbol":"CCC","available_at":"2026-08-24T12:00:00Z","target_median":150.0,"consensus":"BUY","consensus_delta_4w":4.0,"n_analysts":30,"period_kind":"CURRENT"},
    ])


def test_v4_adds_like_for_like_pit_covered_baseline():
    payload = run_study(ledger(), observations(), StudyConfig(analyst_thresholds=(5,)))
    rows = {row["variant"]: row for row in payload["variants"]}
    assert payload["pit_covered_j1_trades"] == 2
    assert rows["J1_BASELINE"]["trades"] == 3
    assert rows["J1_PIT_COVERED_BASELINE"]["trades"] == 2
    assert rows["J1_PIT_COVERED_BASELINE"]["comparison_cohort"] == "PIT_COVERED_J1"


def test_filtered_variant_deltas_use_covered_cohort_not_full_j1():
    payload = run_study(ledger(), observations(), StudyConfig(analyst_thresholds=(5,)))
    rows = {row["variant"]: row for row in payload["variants"]}
    covered = rows["J1_PIT_COVERED_BASELINE"]
    target = rows["J1_TARGET_GT_20"]
    assert covered["expectancy_pct_per_trade"] == 0.5
    assert target["expectancy_pct_per_trade"] == 0.5
    assert target["delta_expectancy_pct_vs_pit_covered_j1"] == 0.0
    assert target["comparison_cohort"] == "PIT_COVERED_J1"
    assert target["comparable_cohort_trades"] == 2


def test_missing_pit_is_reported_but_not_counted_as_filter_alpha():
    payload = run_study(ledger(), observations(), StudyConfig(analyst_thresholds=(5,)))
    row = {row["variant"]: row for row in payload["variants"]}["J1_TARGET_GT_20"]
    assert row["pit_unassessable_trades_vs_j1"] == 1
    assert row["winners_unassessable_missing_pit_vs_j1"] == 1
    assert row["filter_rejections_among_pit"] == 0
    assert row["delta_expectancy_pct_vs_pit_covered_j1"] == 0.0


def test_analyst_filter_is_evaluated_only_after_target_consensus_revision_stack():
    payload = run_study(ledger(), observations(), StudyConfig(analyst_thresholds=(5, 10, 15)))
    rows = {row["variant"]: row for row in payload["variants"]}
    assert rows["J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING"]["trades"] == 2
    assert rows["J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING_ANALYSTS_GE_5"]["trades"] == 1
    assert rows["J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING_ANALYSTS_GE_10"]["trades"] == 1
    assert rows["J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING_ANALYSTS_GE_15"]["trades"] == 0


def test_policy_forbids_full_j1_headline_comparison_for_filtered_variants():
    payload = run_study(ledger(), observations(), StudyConfig(analyst_thresholds=(5,)))
    assert payload["version"] == "HEBDO_META_CONSENSUS_GATE_AUDIT73_V4"
    assert payload["policy"]["like_for_like_pit_baseline_required"] is True
    assert payload["policy"]["filtered_headline_comparison_to_full_j1_forbidden"] is True
