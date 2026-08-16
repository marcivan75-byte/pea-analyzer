from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ipo_v1_2_governance_keeps_shadow_and_existing_weights() -> None:
    config = json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))
    assert config["version"] == "IPO_RADAR_V1.2"
    assert config["mode"] == "SHADOW_ADVISORY_ONLY"
    assert config["net_score_weights"] == {"opportunity": 0.60, "risk_inverse": 0.40}
    assert abs(sum(config["opportunity_weights"].values()) - 100.0) < 1e-9
    assert abs(sum(config["risk_weights"].values()) - 100.0) < 1e-9
    assert config["governance"]["live_orders_enabled"] is False
    assert config["governance"]["can_create_buy"] is False
    assert config["governance"]["t1_t2_forbidden"] is True
    assert config["governance"]["promotion_requires_dedicated_pit_oos_backtest"] is True
    assert config["governance"]["automatic_v1_2_criteria_shadow_until_pit_oos"] is True


def test_ipo_v1_2_financial_and_valuation_evidence_policy_is_conservative() -> None:
    config = json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))
    deep = config["v1_2_deep_dd"]
    assert deep["inline_xbrl_enabled"] is True
    assert deep["financial_evidence_priority"] == "PROSPECTUS_INLINE_XBRL_FIRST_COMPANYFACTS_FALLBACK"
    assert deep["post_ipo_balance_sheet_requires_detected_net_proceeds"] is True
    assert deep["absolute_valuation_diagnostic_shadow_only"] is True
    assert deep["peer_relative_valuation_must_not_be_inferred_from_absolute_multiple"] is True
    assert deep["liquidity_hard_block_upper_bound_runway_lt_years"] == 1.0
