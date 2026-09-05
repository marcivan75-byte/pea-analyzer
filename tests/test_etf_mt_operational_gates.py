from __future__ import annotations

import json
from pathlib import Path

from v182.decision.etf_mt_operational_gates import apply_operational_gates


def _config() -> dict:
    path = Path("config/ETF_MT_PROCESS_V21.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_process_config_does_not_touch_frozen_core():
    cfg = _config()
    assert cfg["locked_reference_core"] == "V20.8.1_ETF_MT_HIGH_PRECISION"
    assert cfg["design"]["do_not_retune_38_pit_weights"] is True
    assert cfg["design"]["live_orders_enabled"] is False
    assert cfg["books"]["PRECISION"]["exit"]["target_return"] == 0.04
    assert cfg["books"]["THESIS_MT"]["exit"]["target_return"] is None
    assert cfg["books"]["THESIS_MT"]["historical_performance_attribution"] == "NONE"


def test_block_small_aum_and_missing_thesis():
    cfg = _config()
    result = apply_operational_gates(
        {
            "pea_eligible": True,
            "fund_total_assets_eur_m": 20.0,
            "ter_pct": 0.20,
            "precision_selected": True,
            "thesis": "short",
            "invalidation": "no",
        },
        cfg,
    )
    assert result.status == "BLOCK"
    assert "AUM_BELOW_50M" in result.reasons
    assert "THESIS_MISSING" in result.reasons
    assert "INVALIDATION_MISSING" in result.reasons


def test_pass_complete_current_snapshot():
    cfg = _config()
    thesis = "Exposition Europe value cyclique sur 36 mois, catalyseur revision benefices banques et industrials, valorisation encore decotee vs US."
    invalidation = "Sortie si PMI < 48 deux mois ou banks vs SX7E casse le range."
    result = apply_operational_gates(
        {
            "pea_eligible": True,
            "fund_total_assets_eur_m": 420.0,
            "ter_pct": 0.18,
            "category": "EUROPE",
            "precision_selected": True,
            "thesis": thesis,
            "invalidation": invalidation,
        },
        cfg,
        stale_dynamic_days=2,
    )
    assert result.status == "PASS"
    assert result.allowed is True


def test_stale_data_blocks_even_if_score_would_pass():
    cfg = _config()
    result = apply_operational_gates(
        {
            "pea_eligible": True,
            "fund_total_assets_eur_m": 800.0,
            "precision_selected": True,
            "thesis": "x" * 50,
            "invalidation": "y" * 25,
        },
        cfg,
        stale_dynamic_days=12,
    )
    assert result.status == "BLOCK"
    assert "STALE_DYNAMIC_DATA" in result.reasons


def test_gates_cannot_create_precision_signal():
    cfg = _config()
    result = apply_operational_gates(
        {
            "pea_eligible": True,
            "fund_total_assets_eur_m": 2000.0,
            "precision_selected": False,
            "thesis": "x" * 50,
            "invalidation": "y" * 25,
        },
        cfg,
    )
    assert result.status == "BLOCK"
    assert "NOT_PRECISION_BUY_CANDIDATE" in result.reasons
