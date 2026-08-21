from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json

import numpy as np
import pandas as pd

from v182.features.action_ct_context_v22_1 import build_action_ct_context_overlay
from v182.features.action_ct_v22_1 import compute_action_ct_snapshot_v22_1
from v182.features.ct_math import mean_available, weighted_score
from v182.features.sector_rotation import build_rotation_observations
from v182.reporting.action_ct_shadow_run_v22_1 import _validate_master_schema


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ACTION_CT_V22_1_0_SHADOW.json").read_text(encoding="utf-8"))


def _master(rows: int = 500) -> pd.DataFrame:
    sectors = ["Technology", "Industrials", "Health Care", "Consumer", "Energy"]
    return pd.DataFrame(
        {
            "isin": [f"FR{i:010d}" for i in range(rows)],
            "sector": [sectors[i % len(sectors)] for i in range(rows)],
            "distance_high_52w_pct": np.linspace(2.0, 24.0, rows),
            "perf_1m_pct": np.linspace(-8.0, 15.0, rows),
            "perf_3m_pct": np.linspace(-12.0, 30.0, rows),
            "perf_6m_pct": np.linspace(-20.0, 50.0, rows),
            "above_mm50": [i % 3 != 0 for i in range(rows)],
            "above_mm200": [i % 4 != 0 for i in range(rows)],
            "catchup_52w_score": np.linspace(30.0, 90.0, rows),
            "morningstar_rating": [4 if i % 2 == 0 else 3 for i in range(rows)],
            "target_upside_pct_v21": np.linspace(4.0, 25.0, rows),
            "dividend_yield_pct": np.full(rows, 2.0),
        }
    )


def _history() -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-08-21", periods=180)
    close = np.linspace(100.0, 150.0, len(idx))
    close[-8:] = [150.0, 148.0, 146.0, 143.0, 140.0, 138.0, 136.0, 134.0]
    volume = np.full(len(idx), 1_000_000.0)
    return pd.DataFrame(
        {
            "open": close * 0.997,
            "high": close * 1.008,
            "low": close * 0.992,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )


def test_common_math_renormalizes_only_observed_components():
    score, coverage = weighted_score({"a": 80.0, "b": None}, {"a": 0.6, "b": 0.4})
    assert score == 80.0
    assert coverage == 0.6
    assert mean_available([None, 20.0, 40.0]) == 30.0


def test_vectorized_context_overlay_has_coverage_diagnostics_and_runtime_budget():
    master = _master(500)
    start = perf_counter()
    overlay, diag = build_action_ct_context_overlay(master, _cfg())
    elapsed = perf_counter() - start
    assert len(overlay) >= 400
    assert diag["coverage"]["field_coverage_pct"]["relative_strength"] > 90.0
    assert diag["coverage"]["context_richness_pct"] > 0.0
    assert elapsed < 5.0


def test_sector_rotation_parameters_and_concentration_diagnostics_are_exposed():
    cfg = _cfg()
    cfg["context_overlay"]["sector_rotation"]["min_sector_size"] = 10
    observations, sectors, diag = build_rotation_observations(_master(100), cfg=cfg)
    assert observations
    assert not sectors.empty
    assert diag["min_sector_size"] == 10
    assert diag["catchup_distance_scale_pct"] == 25.0
    assert diag["rotation_hhi_10000"] is not None
    assert isinstance(diag["rotation_concentration_warning"], bool)


def test_runtime_patch_does_not_activate_weight_sensitivity_or_production():
    cfg = _cfg()
    assert cfg["runtime_patch_version"] == "ACTION_CT_V22.1.1_PERFORMANCE_OBSERVABILITY_PATCH"
    assert cfg["entry_weights_sensitivity"]["enabled"] is False
    assert cfg["entry_weights_sensitivity"]["activation_requires_holdout_validation"] is True
    assert cfg["governance"]["decision_influence"] == 0.0
    assert cfg["governance"]["runtime_patch_must_not_change_pit_epoch"] is True
    assert cfg["pit_validation"]["epoch"] == "ACTION_CT_V22.1.0_ONLY"


def test_v22_1_exposes_split_risks_asymmetry_and_context_richness_without_orders():
    context = {
        "relative_strength": 80.0,
        "sector_rotation_score": 75.0,
        "action_catchup_score": 70.0,
        "valuation_discount_score": 20.0,
        "theme_weighted_AVCR": 80.0,
        "morningstar_action_score": 80.0,
        "target_upside_growth_score": 75.0,
        "target_upside_gt4_score": 85.0,
        "days_to_earnings": 1.0,
    }
    snap = compute_action_ct_snapshot_v22_1(_history(), _cfg(), context)
    assert snap["valuation_risk_score"] is not None
    assert snap["event_risk_score"] == 100.0
    assert snap["valuation_event_risk_score"] is not None
    assert snap["asymmetric_risk_score"] is not None
    assert snap["context_richness_score"] > 0.0
    assert snap["real_orders_enabled"] is False
    assert snap["fixed_take_profit_enabled"] is False
    assert snap["fixed_stop_loss_enabled"] is False


def test_master_schema_validation_is_fail_closed_for_missing_isin():
    invalid = _validate_master_schema(pd.DataFrame({"ticker": ["AAA.PA"]}))
    valid = _validate_master_schema(pd.DataFrame({"isin": ["FR0000000001"], "yahoo_ticker": ["AAA.PA"]}))
    assert invalid["valid"] is False
    assert invalid["missing_required_columns"] == ["isin"]
    assert valid["valid"] is True
    assert valid["ticker_columns_present"] == ["yahoo_ticker"]
