import pandas as pd

from v182.reporting.criteria_governance_audit import _governance_rows


def test_reference_criterion_is_active_when_available():
    master = pd.DataFrame([{"isin": "FR1", "c1": 1.0}, {"isin": "FR2", "c1": 2.0}])
    reference = {
        "weights": {"CT": {"c1": 1.0}},
        "directions": {"CT": {"c1": "HIGH"}},
    }
    audit = _governance_rows(master, reference, None, "ACTION")
    row = audit.iloc[0]
    assert row["governance_status"] == "ACTIVE"
    assert row["effective_status"] == "ACTIVE"
    assert row["decision_influence"] == 1.0


def test_challenger_only_criterion_is_shadow_and_zero_influence():
    master = pd.DataFrame([{"isin": "FR1", "c1": 1.0, "c2": 3.0}, {"isin": "FR2", "c1": 2.0, "c2": 4.0}])
    reference = {
        "weights": {"CT": {"c1": 1.0}},
        "directions": {"CT": {"c1": "HIGH"}},
    }
    challenger = {
        "weights": {"CT": {"c1": 0.8, "c2": 0.2}},
        "directions": {"CT": {"c1": "HIGH", "c2": "HIGH"}},
    }
    audit = _governance_rows(master, reference, challenger, "ACTION")
    row = audit.loc[audit["criterion"] == "c2"].iloc[0]
    assert row["governance_status"] == "SHADOW"
    assert row["effective_status"] == "SHADOW"
    assert row["decision_influence"] == 0.0
    assert row["promotion_status"] == "BLOCKED_UNTIL_PIT_OOS"


def test_missing_reference_criterion_is_explicit():
    master = pd.DataFrame([{"isin": "FR1", "other": 1.0}])
    reference = {
        "weights": {"CT": {"missing": 1.0}},
        "directions": {"CT": {"missing": "HIGH"}},
    }
    audit = _governance_rows(master, reference, None, "ACTION")
    row = audit.iloc[0]
    assert row["governance_status"] == "ACTIVE"
    assert row["data_status"] == "MISSING"
    assert row["effective_status"] == "MISSING"
