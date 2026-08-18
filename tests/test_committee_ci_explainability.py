from pathlib import Path

import pandas as pd

from v182.reporting.committee_ci_explainability import _generic_details, run


def test_generic_details_reconstructs_cross_sectional_score_exactly():
    source = pd.DataFrame(
        [
            {"isin": "FR0001", "name": "A", "c1": 10.0, "c2": 5.0},
            {"isin": "FR0002", "name": "B", "c1": 20.0, "c2": 1.0},
        ]
    )
    selected = pd.DataFrame(
        [
            {"asset_class": "ACTION", "horizon": "CT", "isin": "FR0001", "name": "A", "decision": "BUY_CANDIDATE", "score": 50.0},
        ]
    )
    registry = {
        "weights": {"CT": {"c1": 0.5, "c2": 0.5}},
        "directions": {"CT": {"c1": "HIGH", "c2": "HIGH"}},
        "horizons": {"CT": {"minimum_weighted_coverage": 0.7}},
    }
    detail = _generic_details(source, selected, registry, "ACTION", ["CT"])
    assert len(detail) == 2
    assert abs(detail["effective_weight_pct"].sum() - 100.0) < 1e-9
    assert abs(detail["weighted_contribution_points"].sum() - 50.0) < 1e-9


def test_missing_criterion_is_explicit_and_not_renormalized_as_available():
    source = pd.DataFrame(
        [
            {"isin": "FR0001", "name": "A", "c1": 10.0},
            {"isin": "FR0002", "name": "B", "c1": 20.0},
        ]
    )
    selected = pd.DataFrame(
        [{"asset_class": "ACTION", "horizon": "CT", "isin": "FR0001", "name": "A", "decision": "WATCH", "score": 50.0}]
    )
    registry = {
        "weights": {"CT": {"c1": 0.5, "missing": 0.5}},
        "directions": {"CT": {"c1": "HIGH", "missing": "HIGH"}},
        "horizons": {"CT": {"minimum_weighted_coverage": 0.7}},
    }
    detail = _generic_details(source, selected, registry, "ACTION", ["CT"])
    missing = detail.loc[detail["criterion"] == "missing"].iloc[0]
    active = detail.loc[detail["criterion"] == "c1"].iloc[0]
    assert missing["criterion_status"] == "MISSING"
    assert missing["effective_weight_pct"] == 0.0
    assert active["effective_weight_pct"] == 100.0


def test_run_fails_closed_without_committee_decisions(tmp_path: Path):
    result = run(tmp_path)
    assert result["status"] == "BLOCKED_COMMITTEE_DECISIONS_MISSING"
    assert result["real_orders_enabled"] is False
