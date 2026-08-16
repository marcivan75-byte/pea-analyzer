import numpy as np
import pandas as pd

from v182.reporting.sector_rotation_v2_validation_run import _basket_metrics, build_outcome_observations


PROTOCOL = {
    "primary_horizon_days": 3,
    "periods": {
        "VALIDATION_OOS": {"start": "2026-09-01", "end": "2026-12-31"},
        "DIAGNOSTIC_OOS": {"start": "2027-01-01", "end": "2027-04-30"},
        "final_holdout_start": "2027-05-01",
    },
    "outcomes": {
        "minimum_constituent_price_coverage": 0.66,
        "minimum_constituents": 2,
    },
}


def _close(values):
    return pd.Series(values, index=pd.bdate_range("2026-09-01", periods=len(values), tz="UTC"), dtype=float)


def test_basket_metrics_uses_frozen_equal_weight_paths():
    prices = {
        "A.PA": _close([100, 101, 102, 103, 104]),
        "B.PA": _close([200, 202, 204, 206, 208]),
        "C.PA": _close([50, 49, 48, 47, 46]),
    }
    metrics = _basket_metrics(["A.PA", "B.PA", "C.PA"], pd.Timestamp("2026-09-01", tz="UTC"), prices, PROTOCOL)
    assert metrics is not None
    expected = np.mean([1.03, 1.03, 0.94])
    assert abs(metrics["forward_return_pct"] - (expected - 1) * 100) < 1e-9
    assert metrics["constituents_used"] == 3


def test_outcomes_do_not_calculate_final_holdout():
    signals = pd.DataFrame(
        [
            {
                "sector": "Tech",
                "as_of": "2026-09-01",
                "model_version": "V2",
                "RARS": 80,
                "RLS": 80,
                "AVCR": 40,
                "DQS": 90,
                "v1_sector_rotation_score": 70,
            },
            {
                "sector": "Tech",
                "as_of": "2027-05-03",
                "model_version": "V2",
                "RARS": 90,
                "RLS": 90,
                "AVCR": 20,
                "DQS": 95,
                "v1_sector_rotation_score": 75,
            },
        ]
    )
    members = pd.DataFrame(
        [
            {"sector": "Tech", "as_of": "2026-09-01", "model_version": "V2", "yahoo_ticker": "A.PA"},
            {"sector": "Tech", "as_of": "2026-09-01", "model_version": "V2", "yahoo_ticker": "B.PA"},
            {"sector": "Tech", "as_of": "2027-05-03", "model_version": "V2", "yahoo_ticker": "A.PA"},
            {"sector": "Tech", "as_of": "2027-05-03", "model_version": "V2", "yahoo_ticker": "B.PA"},
        ]
    )
    prices = {
        "A.PA": _close([100, 101, 102, 103, 104]),
        "B.PA": _close([100, 100, 101, 101, 102]),
    }
    observations, diagnostic = build_outcome_observations(signals, members, prices, PROTOCOL)
    assert len(observations) == 1
    assert observations["as_of"].max() < pd.Timestamp("2027-05-01", tz="UTC")
    assert diagnostic["holdout_signals_locked"] == 1


def test_immature_sector_is_retained_for_forward_coverage_denominator():
    signals = pd.DataFrame(
        [
            {
                "sector": "Tech",
                "as_of": "2026-09-03",
                "model_version": "V2",
                "RARS": 80,
                "RLS": 80,
                "AVCR": 40,
                "DQS": 90,
                "v1_sector_rotation_score": 70,
            }
        ]
    )
    members = pd.DataFrame(
        [
            {"sector": "Tech", "as_of": "2026-09-03", "model_version": "V2", "yahoo_ticker": "A.PA"},
            {"sector": "Tech", "as_of": "2026-09-03", "model_version": "V2", "yahoo_ticker": "B.PA"},
        ]
    )
    prices = {
        "A.PA": _close([100, 101, 102, 103]),
        "B.PA": _close([100, 101, 102, 103]),
    }
    observations, diagnostic = build_outcome_observations(signals, members, prices, PROTOCOL)
    assert len(observations) == 1
    assert observations.iloc[0]["outcome_status"] == "IMMATURE_OR_LOW_PRICE_COVERAGE"
    assert pd.isna(observations.iloc[0]["forward_return_pct_3d"])
    assert diagnostic["mature_observations"] == 0


def test_constituents_without_tickers_count_against_price_coverage():
    prices = {
        "A.PA": _close([100, 101, 102, 103]),
        "B.PA": _close([100, 101, 102, 103]),
        "C.PA": _close([100, 101, 102, 103]),
    }
    metrics = _basket_metrics(
        ["A.PA", "B.PA", "C.PA"],
        pd.Timestamp("2026-09-01", tz="UTC"),
        prices,
        PROTOCOL,
        expected_constituents=5,
    )
    assert metrics is None


def test_model_version_lock_excludes_mixed_model_evidence():
    protocol = {**PROTOCOL, "model_version_lock": "V2"}
    signals = pd.DataFrame(
        [
            {
                "sector": "Tech",
                "as_of": "2026-09-01",
                "model_version": "V2",
                "RARS": 80,
                "RLS": 80,
                "AVCR": 40,
                "DQS": 90,
                "v1_sector_rotation_score": 70,
            },
            {
                "sector": "Energy",
                "as_of": "2026-09-01",
                "model_version": "V3",
                "RARS": 90,
                "RLS": 90,
                "AVCR": 20,
                "DQS": 95,
                "v1_sector_rotation_score": 75,
            },
        ]
    )
    members = pd.DataFrame(
        [
            {"sector": "Tech", "as_of": "2026-09-01", "model_version": "V2", "yahoo_ticker": "A.PA"},
            {"sector": "Tech", "as_of": "2026-09-01", "model_version": "V2", "yahoo_ticker": "B.PA"},
            {"sector": "Energy", "as_of": "2026-09-01", "model_version": "V3", "yahoo_ticker": "A.PA"},
            {"sector": "Energy", "as_of": "2026-09-01", "model_version": "V3", "yahoo_ticker": "B.PA"},
        ]
    )
    prices = {
        "A.PA": _close([100, 101, 102, 103, 104]),
        "B.PA": _close([100, 101, 102, 103, 104]),
    }
    observations, diagnostic = build_outcome_observations(signals, members, prices, protocol)
    assert observations["model_version"].unique().tolist() == ["V2"]
    assert diagnostic["signals_excluded_version_mismatch"] == 1
    assert diagnostic["members_excluded_version_mismatch"] == 2
