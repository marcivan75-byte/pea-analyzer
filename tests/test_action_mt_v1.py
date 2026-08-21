from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.features.action_mt_v1 import PositionState, compute_action_mt_snapshot, exit_decision


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ACTION_MT_V1_0_0_SHADOW.json").read_text(encoding="utf-8"))


def _history(periods: int = 320, turnover: float = 4_000_000.0) -> pd.DataFrame:
    close = np.linspace(80.0, 140.0, periods) + np.sin(np.linspace(0, 16, periods))
    volume = turnover / close
    return pd.DataFrame({"close": close, "volume": volume})


def _context() -> dict:
    return {
        "quality_score": 82.0,
        "profitability_score": 78.0,
        "balance_sheet_score": 80.0,
        "earnings_growth_score": 76.0,
        "revenue_growth_score": 72.0,
        "free_cash_flow_growth_score": 75.0,
        "valuation_discount_score": 68.0,
        "analyst_revisions_score": 74.0,
        "target_upside_growth_score": 70.0,
        "sector_rotation_score": 72.0,
        "sector_macro_score": 65.0,
        "macro_evidence_sufficient": True,
        "market_regime_score": 70.0,
    }


def test_config_weights_and_shadow_guards():
    cfg = _cfg()
    assert abs(sum(cfg["score_weights"].values()) - 1.0) < 1e-12
    assert cfg["governance"]["real_orders_enabled"] is False
    assert cfg["governance"]["structural_snapshot_can_promote_signal"] is False
    assert cfg["data_policy"]["missing_values_are_never_neutral"] is True


def test_strong_medium_term_profile_is_selected_in_shadow():
    snap = compute_action_mt_snapshot(_history(), _cfg(), _context())
    assert snap["status"] == "SUCCESS_SHADOW"
    assert snap["decision"] in {"ENTRY_READY_SHADOW", "ENTRY_STRONG_SHADOW"}
    assert snap["score_coverage"] == 1.0
    assert snap["return_6m_pct"] > 0
    assert snap["real_orders_enabled"] is False


def test_missing_structural_context_fails_closed_on_coverage():
    snap = compute_action_mt_snapshot(_history(), _cfg(), {})
    assert snap["decision"] == "DATA_INSUFFICIENT"
    assert snap["score_coverage"] < _cfg()["gates"]["minimum_score_coverage"]


def test_low_liquidity_blocks_entry():
    snap = compute_action_mt_snapshot(_history(turnover=100_000.0), _cfg(), _context())
    assert snap["decision"] == "RISK_BLOCKED_SHADOW"
    assert "LIQUIDITY_BLOCK" in snap["warnings"]


def test_history_gate_is_strict():
    snap = compute_action_mt_snapshot(_history(periods=120), _cfg(), _context())
    assert snap["status"] == "DATA_INSUFFICIENT"


def test_exit_policy_uses_stop_time_and_trailing_close_only():
    cfg = _cfg()
    assert exit_decision(PositionState(100, 81, 10, 110), cfg) == "HARD_STOP_CLOSE_SHADOW"
    assert exit_decision(PositionState(100, 110, 252, 120), cfg) == "TIME_REVIEW_CLOSE_SHADOW"
    assert exit_decision(PositionState(100, 112, 60, 130), cfg) == "TRAILING_STOP_CLOSE_SHADOW"
    assert exit_decision(PositionState(100, 108, 20, 110), cfg) == "HOLD_SHADOW"

