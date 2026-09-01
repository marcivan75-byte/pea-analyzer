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


def test_missing_pit_is_not_misreported_as_filter_rejection():
    payload = run_study(ledger(), observations(), StudyConfig(analyst_thresholds=(5,)))
    row = {r["variant"]: r for r in payload["variants"]}["J1_TARGET_GT_20"]
    assert row["pit_unassessable_trades_vs_j1"] == 1
    assert row["winners_unassessable_missing_pit_vs_j1"] == 1
    assert row["filter_rejections_among_pit"] == 0
    assert row["winners_filtered_out_among_pit"] == 0


def test_no_entry_price_means_fail_closed_not_published_upside_fallback():
    bad = ledger().drop(columns=["entry_price"])
    with pytest.raises(ValueError, match="ENTRY_PRICE_REQUIRED"):
        attach_latest_pit_snapshot(bad, observations())


def test_native_tabport_walkforward_fractional_returns_are_scaled_to_percent():
    wf = pd.DataFrame([
        {"ticker":"AAA","date":"2026-08-24T08:00:00Z","entry_outcome_price":100.0,"outcome_return":0.25,"hit_stop":False,"true_fp_durable":0},
        {"ticker":"BBB","date":"2026-08-24T08:00:00Z","entry_outcome_price":100.0,"outcome_return":-0.09,"hit_stop":True,"true_fp_durable":1},
    ])
    joined = attach_latest_pit_snapshot(wf, observations())
    assert joined.loc[joined.symbol.eq("AAA"), "return_pct"].iloc[0] == 25.0
    assert joined.loc[joined.symbol.eq("BBB"), "return_pct"].iloc[0] == -9.0
    payload = run_study(wf, observations(), StudyConfig(analyst_thresholds=(5,)))
    baseline = payload["variants"][0]
    assert baseline["stops"] == 1
    assert baseline["durable_false_positives"] == 1
    assert baseline["durable_false_positive_definition"] == "TABPORT_LOCKED_TRUE_FP_DURABLE"


def test_csv_false_strings_do_not_become_true():
    wf = pd.DataFrame([
        {"ticker":"AAA","date":"2026-08-24T08:00:00Z","entry_outcome_price":100.0,"outcome_return":0.10,"hit_stop":"False","true_fp_durable":"False","endpoint_mark":"False"},
        {"ticker":"BBB","date":"2026-08-24T08:00:00Z","entry_outcome_price":100.0,"outcome_return":-0.09,"hit_stop":"True","true_fp_durable":"True","endpoint_mark":"False"},
    ])
    payload = run_study(wf, observations(), StudyConfig(analyst_thresholds=(5,)))
    baseline = payload["variants"][0]
    assert baseline["trades"] == 2
    assert baseline["stops"] == 1
    assert baseline["durable_false_positives"] == 1


def test_actual_tabport_pnl_drives_pf_and_initial_capital_return():
    tab = pd.DataFrame([
        {"ticker":"AAA","entry_date":"2026-08-24T08:00:00Z","exit_date":"2026-08-25T08:00:00Z","entry_price":100.0,"return_net":0.10,"pnl_net":450.0,"stop_declenche":"False"},
        {"ticker":"BBB","entry_date":"2026-08-24T08:00:00Z","exit_date":"2026-08-26T08:00:00Z","entry_price":100.0,"return_net":-0.09,"pnl_net":-405.0,"stop_declenche":"True"},
    ])
    payload = run_study(tab, observations(), StudyConfig(analyst_thresholds=(5,), initial_capital_eur=65000.0))
    baseline = payload["variants"][0]
    assert baseline["pnl_basis"] == "ACTUAL_TABPORT_PNL_NET"
    assert baseline["profit_factor"] == round(450.0 / 405.0, 4)
    assert baseline["pnl_eur"] == 45.0
    assert baseline["return_on_initial_capital_pct"] == round(45.0 / 65000.0 * 100.0, 4)
    assert baseline["max_drawdown_basis"] == "REALIZED_EXIT_PNL_NOT_MARK_TO_MARKET"
