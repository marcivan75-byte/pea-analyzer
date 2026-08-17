import numpy as np
import pandas as pd

from v182.risk.beta_metrics import compute_beta_metrics


def test_very_low_r2_beta_fails_closed_but_preserves_audit_correlation():
    idx = pd.date_range("2024-01-01", periods=320, freq="B")
    rng = np.random.default_rng(20260817)
    benchmark = pd.Series(rng.normal(0.0002, 0.01, len(idx)), index=idx)
    independent = pd.Series(rng.normal(0.0001, 0.08, len(idx)), index=idx)

    result = compute_beta_metrics(independent, benchmark)

    assert result["beta_reliability"] == "VERY_LOW"
    assert result["status"] == "UNRELIABLE_LOW_R2"
    assert result["r2_252d"] is not None
    assert result["correlation_252d"] is not None
    assert result["beta_63d"] is None
    assert result["beta_126d"] is None
    assert result["beta_252d"] is None
    assert result["upside_beta_252d"] is None
    assert result["downside_beta_252d"] is None
    assert result["downside_upside_beta_ratio"] is None
    assert result["beta_stability_span"] is None
    assert result["beta_class"] == "UNRELIABLE"


def test_reliable_beta_remains_available_without_formula_change():
    idx = pd.date_range("2024-01-01", periods=320, freq="B")
    rng = np.random.default_rng(41)
    benchmark = pd.Series(rng.normal(0.0003, 0.01, len(idx)), index=idx)
    asset = 1.25 * benchmark + pd.Series(rng.normal(0.0, 0.002, len(idx)), index=idx)

    result = compute_beta_metrics(asset, benchmark)

    assert result["status"] == "OK"
    assert result["beta_reliability"] in {"MEDIUM", "HIGH"}
    assert result["beta_252d"] is not None
    assert 1.0 < float(result["beta_252d"]) < 1.5
