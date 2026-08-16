from copy import deepcopy

import pandas as pd
import pytest

from v182.backtest.sector_rotation_v2_pit_oos import evaluate_governed_validation


BASE_PROTOCOL = {
    "version": "TEST",
    "primary_horizon_days": 60,
    "periods": {
        "VALIDATION_OOS": {"start": "2026-09-01", "end": "2026-12-31"},
        "DIAGNOSTIC_OOS": {"start": "2027-01-01", "end": "2027-04-30"},
        "final_holdout_start": "2027-05-01",
    },
    "eligibility": {
        "minimum_dqs": 80.0,
        "minimum_sectors_per_snapshot": 6,
        "minimum_v1_coverage": 0.8,
        "minimum_forward_return_coverage": 0.8,
        "minimum_snapshot_spacing_days": 10,
    },
    "ranking": {"top_k": 3},
    "promotion_gates": {
        "minimum_independent_snapshots_each_period": 2,
        "minimum_v2_vs_v1_mean_return_pp": 0.5,
        "minimum_v2_vs_neutral_mean_return_pp": 0.75,
        "maximum_positive_rate_degradation_pp": 5.0,
        "maximum_mean_mae_degradation_pp": 1.0,
        "maximum_p10_return_degradation_pp": 1.0,
    },
    "warnings": {
        "leader_rls_min": 70.0,
        "minimum_flagged_leaders_total": 4,
        "minimum_unflagged_leaders_total": 4,
        "minimum_return_risk_separation_pp": 1.0,
        "minimum_mae_risk_separation_pp": 1.5,
    },
}


def _snapshot(date: str, *, weak_v2: bool = False) -> list[dict]:
    rows = []
    v2_returns = [5.0, 4.5, 4.0, 0.5, 0.0, -0.5]
    if weak_v2:
        v2_returns = [-2.0, -1.5, -1.0, 3.0, 2.5, 2.0]
    v1_scores = [55, 60, 65, 95, 90, 85]
    for idx in range(6):
        flagged = idx in {3, 4}
        rows.append(
            {
                "sector": f"S{idx}",
                "as_of": date,
                "model_version": "V2",
                "RARS": 100 - idx * 5,
                "RLS": 80 if idx < 5 else 68,
                "AVCR": 80 if flagged else 40,
                "DQS": 90,
                "v1_sector_rotation_score": v1_scores[idx],
                "forward_return_pct_60d": v2_returns[idx],
                "mae_pct_60d": -8.0 if flagged else -2.0,
                "promising_but_overvalued": flagged,
            }
        )
    return rows


def _passing_observations() -> pd.DataFrame:
    rows = []
    for date in ("2026-09-01", "2026-09-15", "2027-01-04", "2027-01-18"):
        rows.extend(_snapshot(date))
    return pd.DataFrame(rows)


def test_waits_when_real_pit_history_is_not_mature():
    result = evaluate_governed_validation(pd.DataFrame(_snapshot("2026-08-20")), BASE_PROTOCOL)
    assert result.summary["status"] == "WAIT_FOR_PIT_HISTORY"
    assert result.summary["promotion_ready"] is False
    assert result.snapshot_metrics.empty


def test_pre_holdout_can_pass_but_never_self_promotes():
    result = evaluate_governed_validation(_passing_observations(), BASE_PROTOCOL)
    assert result.summary["status"] == "PRE_HOLDOUT_PASSED_FINAL_HOLDOUT_LOCKED"
    assert result.summary["pre_holdout_pass"] is True
    assert result.summary["promotion_ready"] is False
    assert result.summary["warning_gate"]["pass"] is True
    assert all(result.summary["periods"][period]["pass"] for period in ("VALIDATION_OOS", "DIAGNOSTIC_OOS"))


def test_final_holdout_rows_are_counted_but_not_evaluated():
    baseline = _passing_observations()
    holdout = pd.DataFrame(_snapshot("2027-05-03", weak_v2=True))
    result = evaluate_governed_validation(pd.concat([baseline, holdout], ignore_index=True), BASE_PROTOCOL)
    assert result.summary["holdout_rows_ignored"] == 6
    assert result.summary["pre_holdout_pass"] is True
    assert result.snapshot_metrics["as_of"].max() < pd.Timestamp("2027-05-01", tz="UTC")


def test_bad_v2_ranking_remains_shadow():
    rows = []
    for date in ("2026-09-01", "2026-09-15", "2027-01-04", "2027-01-18"):
        rows.extend(_snapshot(date, weak_v2=True))
    result = evaluate_governed_validation(pd.DataFrame(rows), BASE_PROTOCOL)
    assert result.summary["pre_holdout_pass"] is False
    assert result.summary["status"] == "HOLD_SHADOW_PRE_HOLDOUT_NOT_PASSED"


def test_duplicate_pit_sector_snapshot_is_rejected():
    frame = _passing_observations()
    duplicate = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="DUPLICATE_VALIDATION_OBSERVATION"):
        evaluate_governed_validation(duplicate, deepcopy(BASE_PROTOCOL))
