from __future__ import annotations

import pandas as pd

from v182.reporting.tct_v24_4_pit_lineage import apply_lineage, first_subsequent_close
from v182.reporting.tct_pit_close_ledger import build_close_observations


def _snapshot(rows: int = 4) -> pd.DataFrame:
    out = []
    for i in range(rows):
        out.append(
            {
                "snapshot_key": f"2026-08-19|PREOPEN|FR{i}",
                "snapshot_generated_at_utc": "2026-08-19T06:40:00+00:00",
                "phase": "PREOPEN",
                "as_of_date": "2026-08-18",
                "isin": f"FR{i}",
                "yahoo_ticker": f"T{i}.PA",
                "reference_close": 100.0,
                "movement_potential_score": 80.0 - i,
                "direction_bias_score": 40.0,
                "realized_close_to_close_return_pct": 99.0,
                "realized_abs_return_pct": 99.0,
            }
        )
    return pd.DataFrame(out)


def test_first_subsequent_close_uses_earliest_actual_market_bar():
    closes = pd.DataFrame(
        [
            {"as_of_date": "2026-08-20", "isin": "FR0", "reference_close": 120.0},
            {"as_of_date": "2026-08-19", "isin": "FR0", "reference_close": 105.0},
        ]
    )
    prepared = closes.copy()
    prepared["as_of_date"] = pd.to_datetime(prepared["as_of_date"])
    prepared = prepared.sort_values(["isin", "as_of_date"])
    assert first_subsequent_close(prepared, "FR0", "2026-08-18") == ("2026-08-19", 105.0)


def test_lineage_replaces_latest_seed_style_label_with_true_j_plus_one():
    ledger = _snapshot(1)
    closes = pd.DataFrame(
        [
            {"as_of_date": "2026-08-19", "isin": "FR0", "reference_close": 105.0},
            {"as_of_date": "2026-08-20", "isin": "FR0", "reference_close": 120.0},
        ]
    )
    out, stats = apply_lineage(
        ledger,
        closes,
        minimum_snapshot_coverage=0.80,
        labeled_at_utc="2026-08-20T18:30:00+00:00",
    )
    row = out.iloc[0]
    assert float(row["raw_realized_close_to_close_return_pct"]) == 5.0
    assert float(row["realized_close_to_close_return_pct"]) == 5.0
    assert row["outcome_as_of_date"] == "2026-08-19"
    assert int(row["outcome_step"]) == 1
    assert row["outcome_label_source"] == "FIRST_SUBSEQUENT_OBSERVED_DAILY_CLOSE"
    assert stats["validator_labels"] == 1


def test_snapshot_is_fail_closed_below_minimum_outcome_coverage():
    ledger = _snapshot(4)
    closes = pd.DataFrame(
        [
            {"as_of_date": "2026-08-19", "isin": "FR0", "reference_close": 101.0},
            {"as_of_date": "2026-08-19", "isin": "FR1", "reference_close": 102.0},
            {"as_of_date": "2026-08-19", "isin": "FR2", "reference_close": 103.0},
        ]
    )
    out, stats = apply_lineage(
        ledger,
        closes,
        minimum_snapshot_coverage=0.80,
        labeled_at_utc="2026-08-19T18:30:00+00:00",
    )
    assert stats["raw_next_session_labels"] == 3
    assert stats["validator_labels"] == 0
    assert stats["qualified_snapshots"] == 0
    assert pd.to_numeric(out["realized_abs_return_pct"], errors="coerce").isna().all()
    assert (pd.to_numeric(out["outcome_snapshot_coverage"], errors="coerce") == 0.75).all()


def test_snapshot_becomes_evaluable_once_coverage_gate_is_met():
    ledger = _snapshot(5)
    closes = pd.DataFrame(
        [
            {"as_of_date": "2026-08-19", "isin": f"FR{i}", "reference_close": 100.0 + i}
            for i in range(4)
        ]
    )
    out, stats = apply_lineage(
        ledger,
        closes,
        minimum_snapshot_coverage=0.80,
        labeled_at_utc="2026-08-19T18:30:00+00:00",
    )
    assert stats["raw_next_session_labels"] == 4
    assert stats["validator_labels"] == 4
    assert stats["qualified_snapshots"] == 1
    assert int(out["pit_label_evaluable"].fillna(False).astype(bool).sum()) == 4


def test_prediction_fingerprint_detects_historical_prediction_mutation():
    ledger = _snapshot(1).drop(columns=["realized_close_to_close_return_pct", "realized_abs_return_pct"])
    closes = pd.DataFrame([{"as_of_date": "2026-08-19", "isin": "FR0", "reference_close": 101.0}])
    first, stats1 = apply_lineage(
        ledger,
        closes,
        minimum_snapshot_coverage=0.80,
        labeled_at_utc="2026-08-19T18:30:00+00:00",
    )
    assert stats1["fingerprint_mismatches"] == 0
    mutated = first.copy()
    mutated.loc[0, "movement_potential_score"] = 10.0
    _, stats2 = apply_lineage(
        mutated,
        closes,
        minimum_snapshot_coverage=0.80,
        labeled_at_utc="2026-08-20T18:30:00+00:00",
    )
    assert stats2["fingerprint_mismatches"] == 1


def test_close_ledger_backfills_recent_cached_bars_without_network():
    mapping = pd.DataFrame([{"isin": "FR0", "yahoo_ticker": "T0.PA"}])
    idx = pd.to_datetime(["2026-08-17", "2026-08-18", "2026-08-19"])
    histories = {"T0.PA": pd.DataFrame({"Close": [98.0, 100.0, 105.0]}, index=idx)}
    cfg = {
        "data_policy": {
            "defer_current_day_before_local_close": False,
            "local_close_guard_timezone": "Europe/Paris",
            "local_close_guard_hour": 18,
        }
    }
    rows = build_close_observations(
        mapping,
        histories,
        cfg,
        observed_at_utc="2026-08-19T18:30:00+00:00",
        recent_bars=10,
    )
    assert list(rows["as_of_date"]) == ["2026-08-17", "2026-08-18", "2026-08-19"]
    assert list(rows["reference_close"]) == [98.0, 100.0, 105.0]
    assert rows["network_download_required"].eq(False).all()
