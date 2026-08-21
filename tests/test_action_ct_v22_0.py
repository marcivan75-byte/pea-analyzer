from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd

from v182.features.action_ct_v22_0 import _completed_weekly, compute_action_ct_snapshot


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ACTION_CT_V22_0_0_SHADOW.json").read_text(encoding="utf-8"))


def _history(periods: int = 180, end: str = "2026-08-20") -> pd.DataFrame:
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


def _context() -> dict:
    return {
        "relative_strength": 80.0,
        "sector_rotation_score": 82.0,
        "action_catchup_score": 75.0,
        "market_high_regime_score": 70.0,
        "valuation_discount_score": 20.0,
        "consensus_score_100_v21": 78.0,
        "target_upside_pct_v21": 15.0,
        "consensus_delta_4w": 2.0,
        "net_upgrades_30d_v21": 2.0,
        "news_catalyst_score": 70.0,
        "earnings_catalyst_score": 65.0,
        "days_to_earnings": 10.0,
    }


def test_action_ct_config_is_shadow_and_separate_from_tct():
    cfg = _cfg()
    assert cfg["asset_class"] == "ACTION"
    assert cfg["horizon"] == "CT"
    assert cfg["status"] == "SHADOW_RESEARCH_ONLY"
    assert cfg["governance"]["t1_t2_forbidden"] is True
    assert cfg["governance"]["tct_logic_transfer_forbidden"] is True
    assert cfg["governance"]["fixed_take_profit_enabled"] is False
    assert cfg["governance"]["fixed_stop_loss_enabled"] is False
    assert cfg["governance"]["real_orders_enabled"] is False
    assert cfg["governance"]["holdout_locked"] is True
    assert abs(sum(cfg["entry_weights"].values()) - 1.0) < 1e-12
    assert abs(sum(cfg["exit_risk_weights"].values()) - 1.0) < 1e-12


def test_action_ct_snapshot_uses_daily_weekly_confluence_without_t1_t2():
    snap = compute_action_ct_snapshot(_history(), _cfg(), _context())
    assert snap["status"] == "SUCCESS_SHADOW"
    assert snap["entry_coverage"] >= 0.80
    assert snap["t1_t2_used"] is False
    assert snap["intraday_data_used"] is False
    assert snap["fixed_take_profit_enabled"] is False
    assert snap["fixed_stop_loss_enabled"] is False
    assert snap["real_orders_enabled"] is False
    assert snap["entry_confirmation_count"] >= 3
    assert snap["trend_score"] is not None
    assert snap["weekly_score"] is not None
    assert snap["sector_context_score"] is not None


def test_missing_context_fails_closed_on_entry_coverage():
    snap = compute_action_ct_snapshot(_history(), _cfg(), {})
    assert snap["status"] == "SUCCESS_SHADOW"
    assert snap["entry_coverage"] < _cfg()["shadow_thresholds"]["minimum_entry_coverage"]
    assert snap["entry_state"] == "DATA_INSUFFICIENT"


def test_partial_week_is_not_used_as_completed_week_trend():
    cfg = _cfg()
    base = _history(end="2026-08-20")
    ctx1 = _completed_weekly(base, cfg)
    shocked = base.copy()
    current_week = shocked.index.to_period("W-FRI") == shocked.index[-1].to_period("W-FRI")
    shocked.loc[current_week, ["open", "high", "low", "close"]] *= 2.0
    ctx2 = _completed_weekly(shocked, cfg)
    assert ctx1["current_week_complete"] is False
    assert ctx2["current_week_complete"] is False
    assert ctx1["weekly_close"] == ctx2["weekly_close"]
    assert ctx1["weekly_sma_fast"] == ctx2["weekly_sma_fast"]
    assert ctx1["weekly_return_fast"] == ctx2["weekly_return_fast"]


def test_hot_sector_with_weak_valuation_emits_warning():
    snap = compute_action_ct_snapshot(_history(), _cfg(), _context())
    assert snap["sector_hot_valuation_risk_ct"] is True
    assert "SECTOR_HOT_VALUATION_RISK" in snap["warnings"]


def test_structural_invalidation_is_research_context_not_fixed_stop():
    snap = compute_action_ct_snapshot(_history(), _cfg(), _context())
    assert "structural_invalidation_ct" in snap
    assert snap["fixed_stop_loss_enabled"] is False
