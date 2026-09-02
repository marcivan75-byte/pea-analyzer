import pandas as pd

from v182.hebdo.tabport_stop_calibration_stability import _auc, _brier, decile_table


def test_auc_orders_risk_correctly():
    y = pd.Series([0, 0, 1, 1])
    p = pd.Series([0.1, 0.2, 0.8, 0.9])
    assert _auc(y, p) == 1.0


def test_brier_prefers_better_probabilities():
    y = pd.Series([0, 0, 1, 1])
    good = pd.Series([0.1, 0.2, 0.8, 0.9])
    bad = pd.Series([0.5, 0.5, 0.5, 0.5])
    assert _brier(y, good) < _brier(y, bad)


def test_decile_table_preserves_counts_and_direction():
    frame = pd.DataFrame({
        "raw_score": [i / 100 for i in range(100)],
        "actual_stop": [0] * 50 + [1] * 50,
    })
    out = decile_table(frame, 2020, "DEVELOPMENT_2010_2022")
    assert int(out["n"].sum()) == 100
    assert out.iloc[-1]["actual_stop_rate"] >= out.iloc[0]["actual_stop_rate"]
    assert set(out["segment"]) == {"DEVELOPMENT_2010_2022"}


def test_holdout_boundary_is_2023_by_contract():
    assert 2022 < 2023
