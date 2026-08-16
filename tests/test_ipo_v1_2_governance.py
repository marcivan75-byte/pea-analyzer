from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ipo_v1_4_governance_keeps_shadow_and_existing_weights() -> None:
    config = json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))
    assert config["version"] == "IPO_RADAR_V1.4"
    assert config["mode"] == "SHADOW_ADVISORY_ONLY"
    assert config["net_score_weights"] == {"opportunity": 0.60, "risk_inverse": 0.40}
    assert abs(sum(config["opportunity_weights"].values()) - 100.0) < 1e-9
    assert abs(sum(config["risk_weights"].values()) - 100.0) < 1e-9
    assert config["governance"]["live_orders_enabled"] is False
    assert config["governance"]["can_create_buy"] is False
    assert config["governance"]["t1_t2_forbidden"] is True
    assert config["governance"]["promotion_requires_dedicated_pit_oos_backtest"] is True
    assert config["governance"]["automatic_v1_2_criteria_shadow_until_pit_oos"] is True
    assert config["governance"]["automatic_v1_3_peer_valuation_shadow_until_pit_oos"] is True
    assert config["governance"]["automatic_v1_4_euronext_news_shadow_until_pit_oos"] is True
    assert config["governance"]["peer_data_failure_can_create_score"] is False
    assert config["governance"]["euronext_news_can_create_score"] is False
    assert config["governance"]["pea_eligibility_policy"] == "NEVER_INFER_FINAL_ELIGIBILITY_FROM_LISTING_EXCHANGE_OR_ISIN_PREFIX_ONLY"


def test_ipo_v1_2_financial_and_valuation_evidence_policy_is_conservative() -> None:
    config = json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))
    deep = config["v1_2_deep_dd"]
    assert deep["inline_xbrl_enabled"] is True
    assert deep["financial_evidence_priority"] == "PROSPECTUS_INLINE_XBRL_FIRST_COMPANYFACTS_FALLBACK"
    assert deep["post_ipo_balance_sheet_requires_detected_net_proceeds"] is True
    assert deep["absolute_valuation_diagnostic_shadow_only"] is True
    assert deep["peer_relative_valuation_must_not_be_inferred_from_absolute_multiple"] is True
    assert deep["liquidity_hard_block_upper_bound_runway_lt_years"] == 1.0


def test_ipo_v1_3_real_peer_and_euronext_evidence_fail_closed() -> None:
    config = json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))
    evidence = config["v1_3_evidence"]
    assert evidence["euronext_official_showcase_enabled"] is True
    assert evidence["euronext_sector_is_evidence_not_pea_eligibility"] is True
    assert evidence["peer_provider"] == "FINNHUB"
    assert evidence["peer_grouping"] == "industry"
    assert evidence["peer_minimum_valid_count"] == 3
    assert evidence["peer_multiple_basis"] == "ANNUAL_PRICE_TO_SALES_ONLY"
    assert evidence["same_basis_required"] is True
    assert evidence["peer_api_failure_policy"] == "LEAVE_CRITERION_MISSING_NO_PENALTY_NO_BONUS"
    assert evidence["absolute_multiple_cannot_populate_peer_score"] is True


def test_ipo_v1_4_euronext_regulated_news_is_factual_shadow_only() -> None:
    config = json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))
    evidence = config["v1_4_euronext_regulated_news"]
    assert evidence["enabled"] is True
    assert evidence["source_of_truth"] == "OFFICIAL_EURONEXT_COMPANY_NEWS_PAGE"
    assert evidence["currency_policy"] == "PRESERVE_LOCAL_CURRENCY_NO_CROSS_CURRENCY_SCORING"
    assert evidence["score_influence"] == 0.0
    assert evidence["decision_influence"] == 0.0
    assert evidence["can_create_buy"] is False
    assert evidence["promotion_requires_pit_oos"] is True
