from __future__ import annotations

import json
from pathlib import Path


def test_boursorama_config_is_high_priority_attributed_and_weight_neutral():
    root=Path(__file__).resolve().parents[1]
    cfg=json.loads((root/"config"/"V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    spec=cfg["boursorama_import"]
    assert spec["enabled"] is True
    assert spec["priority"] == "HIGH"
    assert spec["role"] == "PRIORITY_MULTI_BLOCK_ENRICHMENT"
    assert spec["direct_automated_fetch"] is False
    assert spec["evidence_level"] == "B"
    assert spec["missing_policy"] == "NO_IMPUTATION"
    for flag in (
        "bulk_consensus_pages_supported",
        "action_single_title_pages_supported",
        "action_profile_pages_supported",
        "action_key_figures_pages_supported",
        "action_per_palmares_supported",
        "action_dividend_palmares_supported",
        "action_52w_extremes_palmares_supported",
        "action_dividend_calendar_supported",
        "action_company_calendar_supported",
        "action_technical_analysis_context_supported",
        "etf_morningstar_pages_supported",
        "etf_detail_pages_supported",
        "etf_performance_pages_supported",
        "etf_composition_pages_supported",
    ):
        assert spec[flag] is True
    for field in ("consensus_score_100_v21","consensus_delta_4w","target_upside_pct_v21","per_forward_v21"):
        assert field in spec["action_canonical_fields"]
    for field in (
        "boursorama_actual_revenue_k_eur",
        "boursorama_next_corporate_event_date",
        "boursorama_touched_52w_high_flag",
        "boursorama_tec_summary",
    ):
        assert field in spec["action_context_fields"]
        assert field not in spec["action_canonical_fields"]
    for field in ("morningstar_rating","morningstar_category","risk_indicator"):
        assert field in spec["etf_canonical_fields"]
    guards=spec["etf_semantic_guards"]
    assert guards["management_fee_max_is_ter"] is False
    assert guards["risk_requires_explicit_1_to_7_numerator"] is True
    assert guards["new_context_fields_inherit_v20_8_1_performance"] is False
