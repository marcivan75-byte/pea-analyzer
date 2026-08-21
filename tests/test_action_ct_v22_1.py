from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd

from v182.features.action_ct_context_v22_1 import build_action_ct_context_overlay, merge_action_ct_context
from v182.features.action_ct_v22_1 import compute_action_ct_snapshot_v22_1


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ACTION_CT_V22_1_0_SHADOW.json").read_text(encoding="utf-8"))


def _history(periods: int = 180, end: str = "2026-08-21") -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=periods)
    close = np.linspace(100.0, 150.0, periods) + np.sin(np.linspace(0, 10, periods))
    volume = np.full(periods, 1_000_000.0)
    volume[-5:] = 1_350_000.0
    return pd.DataFrame(
        {
            "open": close * 0.996,
            "high": close * 1.008,
            "low": close * 0.992,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )


def _master(rows: int = 30) -> pd.DataFrame:
    data = []
    for i in range(rows):
        data.append(
            {
                "isin": f"FR{i:010d}",
                "name": f"Action {i}",
                "sector": "Technology" if i < rows // 2 else "Industrials",
                "distance_high_52w_pct": 8.0 + i / 10.0,
                "perf_1m_pct": -2.0 + i * 0.4,
                "perf_3m_pct": -3.0 + i * 0.8,
                "perf_6m_pct": -5.0 + i * 1.2,
                "above_mm50": i >= rows // 3,
                "above_mm200": i >= rows // 2,
                "catchup_52w_score": 45.0 + i,
                "morningstar_rating": 4 if i % 2 == 0 else 3,
                "target_upside_pct_v21": 5.0 + i / 2.0,
                "dividend_yield_pct": 2.0,
            }
        )
    return pd.DataFrame(data)


def test_v22_1_config_integrates_more_context_but_stays_shadow():
    cfg = _cfg()
    assert cfg["version"] == "ACTION_CT_V22.1.0_CONTEXT_ENRICHED_SHADOW"
    assert cfg["governance"]["t1_t2_forbidden"] is True
    assert cfg["governance"]["real_orders_enabled"] is False
    assert cfg["governance"]["fixed_take_profit_enabled"] is False
    assert cfg["governance"]["fixed_stop_loss_enabled"] is False
    assert cfg["governance"]["holdout_locked"] is True
    assert abs(sum(cfg["entry_weights"].values()) - 1.0) < 1e-12
    assert abs(sum(cfg["exit_risk_weights"].values()) - 1.0) < 1e-12
    assert "quality_target" in cfg["entry_weights"]
    assert "theme_macro" in cfg["entry_weights"]
    assert "valuation_event_risk" in cfg["exit_risk_weights"]


def test_context_overlay_builds_rotation_relative_strength_and_action_enhancements():
    overlay, diag = build_action_ct_context_overlay(_master(), _cfg())
    assert diag["status"] == "OK"
    assert diag["mapped_actions"] >= 20
    assert "relative_strength" in diag["fields_generated"]
    assert "sector_rotation_score" in diag["fields_generated"]
    assert "morningstar_action_score" in diag["fields_generated"]
    assert "target_upside_growth_score" in diag["fields_generated"]
    sample = overlay["FR0000000029"]
    assert 0.0 <= float(sample["relative_strength"]) <= 100.0
    assert 0.0 <= float(sample["morningstar_action_score"]) <= 100.0


def test_existing_observation_wins_over_derived_fallback():
    row = {"isin": "FR0000000001", "relative_strength": 12.5, "sector_rotation_score": np.nan}
    derived = {"relative_strength": 88.0, "sector_rotation_score": 70.0}
    merged = merge_action_ct_context(row, derived, _cfg())
    assert merged["relative_strength"] == 12.5
    assert merged["sector_rotation_score"] == 70.0


def test_v22_1_engine_uses_quality_theme_macro_without_t1_t2():
    context = {
        "relative_strength": 82.0,
        "sector_rotation_score": 78.0,
        "action_catchup_score": 74.0,
        "market_high_regime_score": 68.0,
        "valuation_discount_score": 55.0,
        "consensus_score_100_v21": 75.0,
        "target_upside_pct_v21": 16.0,
        "target_upside_growth_score": 70.0,
        "target_upside_gt4_score": 90.0,
        "consensus_delta_4w": 2.0,
        "net_upgrades_30d_v21": 2.0,
        "news_catalyst_score": 70.0,
        "earnings_catalyst_score": 65.0,
        "days_to_earnings": 12.0,
        "morningstar_action_score": 80.0,
        "theme_rotation_exposure_score": 75.0,
        "theme_risk_adjusted_score": 72.0,
        "theme_confluence_score": 75.0,
        "theme_weighted_AVCR": 30.0,
        "sector_macro_score": 68.0,
        "macro_evidence_sufficient": True,
    }
    snap = compute_action_ct_snapshot_v22_1(_history(), _cfg(), context)
    assert snap["status"] == "SUCCESS_SHADOW"
    assert snap["version_engine"] == "ACTION_CT_V22.1.0_CONTEXT_ENRICHED_SHADOW"
    assert snap["quality_target_score"] is not None
    assert snap["theme_macro_score"] is not None
    assert snap["entry_coverage"] >= 0.80
    assert snap["t1_t2_used"] is False
    assert snap["intraday_data_used"] is False
    assert snap["fixed_take_profit_enabled"] is False
    assert snap["fixed_stop_loss_enabled"] is False
    assert snap["real_orders_enabled"] is False


def test_theme_overvaluation_and_adverse_macro_create_context_warning():
    context = {
        "relative_strength": 80.0,
        "sector_rotation_score": 80.0,
        "action_catchup_score": 75.0,
        "market_high_regime_score": 70.0,
        "valuation_discount_score": 20.0,
        "consensus_score_100_v21": 75.0,
        "target_upside_growth_score": 70.0,
        "target_upside_gt4_score": 85.0,
        "morningstar_action_score": 80.0,
        "theme_rotation_exposure_score": 80.0,
        "theme_risk_adjusted_score": 60.0,
        "theme_confluence_score": 75.0,
        "theme_weighted_AVCR": 80.0,
        "sector_macro_score": 25.0,
        "macro_evidence_sufficient": True,
        "days_to_earnings": 10.0,
    }
    snap = compute_action_ct_snapshot_v22_1(_history(), _cfg(), context)
    assert snap["theme_overvaluation_risk_ct"] is True
    assert snap["macro_context_adverse_ct"] is True
    assert "THEME_OVERVALUATION_RISK" in snap["warnings"]
    assert "MACRO_CONTEXT_ADVERSE" in snap["warnings"]
    assert snap["entry_state"] in {"WAIT_CONTEXT_RISK_SHADOW", "WAIT_RISK_SHADOW", "WAIT_PULLBACK_SHADOW", "ENTRY_CONFLICT_SHADOW"}


def test_macro_without_sufficient_evidence_is_not_scored():
    context = {
        "relative_strength": 75.0,
        "sector_rotation_score": 75.0,
        "action_catchup_score": 70.0,
        "morningstar_action_score": 80.0,
        "target_upside_growth_score": 75.0,
        "sector_macro_score": 95.0,
        "macro_evidence_sufficient": False,
    }
    snap = compute_action_ct_snapshot_v22_1(_history(), _cfg(), context)
    assert snap["status"] == "SUCCESS_SHADOW"
    assert snap["theme_macro_score"] is None
