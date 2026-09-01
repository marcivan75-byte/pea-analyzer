import pandas as pd
import pytest

from v182.backtest.hebdo_meta_consensus_gate_audit73 import (
    StudyConfig,
    attach_latest_pit_snapshot,
    run_study,
)


def ledger():
    return pd.DataFrame([
        {"symbol":"AAA","decision_at":"2026-08-24T08:00:00Z","entry_price":100.0,"return_pct":10.0,"exit_category":"D_REVERSAL"},
        {"symbol":"BBB","decision_at":"2026-08-24T08:00:00Z","entry_price":100.0,"return_pct":-9.0,"exit_category":"PROTECTIVE_STOP"},
        {"symbol":"CCC","decision_at":"2026-08-24T08:00:00Z","entry_price":100.0,"return_pct":25.0,"exit_category":"D_REVERSAL"},
    ])


def observations():
    return pd.DataFrame([
        {"symbol":"AAA","available_at":"2026-08-23T18:00:00Z","target_median":125.0,"consensus":"BUY","consensus_delta_4w":2.0,"n_analysts":12,"period_kind":"CURRENT"},
        {"symbol":"AAA","available_at":"2026-08-23T18:00:00Z","target_median":140.0,"consensus":"STRONG_BUY","consensus_delta_4w":9.0,"n_analysts":12,"period_kind":"RELATIVE"},
        {"symbol":"BBB","available_at":"2026-08-23T18:00:00Z","target_median":130.0,"consensus":"BUY","consensus_delta_4w":1.0,"n_analysts":4,"period_kind":"CURRENT"},
        {"symbol":"CCC","available_at":"2026-08-24T12:00:00Z","target_median":150.0,"consensus":"BUY","consensus_delta_4w":4.0,"n_analysts":30,"period_kind":"CURRENT"},
    ])


def test_relative_factset_row_cannot_masquerade_as_current_state():
    joined = attach_latest_pit_snapshot(ledger(), observations())
    aaa = joined[joined.symbol.eq("AAA")].iloc[0]
    assert aaa.pit_target_median == 125.0
    assert aaa.pit_target_upside_pct == 25.0


def test_snapshot_collected_after_j1_is_not_backfilled():
    joined = attach_latest_pit_snapshot(ledger(), observations())
    ccc = joined[joined.symbol.eq("CCC")].iloc[0]
    assert not bool(ccc.pit_snapshot_available)


def test_target_upside_is_recomputed_from_historical_target_and_j1_price():
    joined = attach_latest_pit_snapshot(ledger(), observations())
    aaa = joined[joined.symbol.eq("AAA")].iloc[0]
    assert aaa.pit_target_upside_pct == 25.0


def test_variant_stack_measures_target_consensus_revision_and_analyst_thresholds():
    payload = run_study(ledger(), observations(), StudyConfig(analyst_thresholds=(5, 10, 15)))
    rows = {r["variant"]: r for r in payload["variants"]}
    assert rows["J1_BASELINE"]["trades"] == 3
    assert rows["J1_TARGET_GT_20"]["trades"] == 2
    assert rows["J1_TARGET_GT_20_POSITIVE_CONSENSUS"]["trades"] == 2
    assert rows["J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING"]["trades"] == 2
    assert rows["J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING_ANALYSTS_GE_5"]["trades"] == 1
    assert rows["J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING_ANALYSTS_GE_15"]["trades"] == 0


def test_no_entry_price_means_fail_closed_not_published_upside_fallback():
    bad = ledger().drop(columns=["entry_price"])
    with pytest.raises(ValueError, match="ENTRY_PRICE_REQUIRED"):
        attach_latest_pit_snapshot(bad, observations())
