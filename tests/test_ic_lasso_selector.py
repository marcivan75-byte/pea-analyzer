import numpy as np
import pandas as pd
import pytest

from v182.scoring.ic_lasso_selector import (
    build_governed_weights,
    compute_information_coefficient,
    lasso_select_features,
)


def test_information_coefficient_requires_30_observations():
    x = pd.DataFrame({"f": np.arange(29, dtype=float)})
    y = pd.Series(np.arange(29, dtype=float))
    out = compute_information_coefficient(x, y)
    assert out.loc[0, "n"] == 29
    assert np.isnan(out.loc[0, "IC"])


def test_information_coefficient_detects_monotonic_signal():
    x = pd.DataFrame({"f": np.arange(40, dtype=float)})
    y = pd.Series(np.arange(40, dtype=float))
    out = compute_information_coefficient(x, y)
    assert out.loc[0, "IC"] == pytest.approx(1.0)


def test_lasso_fails_closed_below_100_rows():
    x = pd.DataFrame({"f": np.arange(99, dtype=float)})
    y = pd.Series(np.arange(99, dtype=float))
    with pytest.raises(ValueError, match="Pas assez de données"):
        lasso_select_features(x, y)


def test_lasso_selects_signal_and_governed_weights_sum_to_one():
    rng = np.random.default_rng(42)
    n = 180
    signal = rng.normal(size=n)
    noise = rng.normal(size=n)
    y = pd.Series(0.25 * signal + rng.normal(scale=0.03, size=n))
    x = pd.DataFrame({"signal": signal, "noise": noise})

    selected, alpha, _ = lasso_select_features(x, y, cv=5, min_abs_coef=1e-4)
    assert alpha >= 0
    assert "signal" in selected["feature"].tolist()

    governed = build_governed_weights(selected)
    assert sum(item["weight"] for item in governed.values()) == pytest.approx(1.0)
    assert governed["signal"]["direction"] == "LONG"
    assert governed["signal"]["training_scale"] > 0
    assert np.isfinite(governed["signal"]["training_mean"])


def test_governed_weights_refuse_missing_training_scale():
    selected = pd.DataFrame([{"feature": "f", "coef_lasso_standardized": 0.2, "mean": 1.0}])
    with pytest.raises(ValueError, match="Colonnes manquantes"):
        build_governed_weights(selected)
