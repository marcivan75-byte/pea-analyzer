from __future__ import annotations

from pathlib import Path
import json

from v182.audit import weekly_v4_governance as audit


def test_v4_governance_audit_passes_complete_reference():
    result = audit.run(Path("."), write=False)
    assert result["status"] == "PASS", result["fatal_failures"]
    assert result["failed"] == 0
    assert result["check_count"] >= 35


def test_v4_has_exactly_five_ordered_audit_domains():
    cfg = json.loads(Path("config/WEEKLY_V4_GOVERNANCE.json").read_text(encoding="utf-8"))
    assert cfg["audits"]["required_iterations"] == 5
    assert cfg["audits"]["domains"] == [
        "REFERENTIALS_AND_INVARIANTS",
        "SOURCE_IDENTITY_FRESHNESS_AND_FACTUALITY",
        "CRITERIA_WEIGHTS_THRESHOLDS_AND_CALIBRATION",
        "CODE_TESTS_AND_CI",
        "ADVERSARIAL_REPRODUCIBILITY_AND_RELEASE",
    ]


def test_reference_weights_are_frozen_without_pit_oos_evidence():
    cfg = json.loads(Path("config/WEEKLY_V4_GOVERNANCE.json").read_text(encoding="utf-8"))
    policy = cfg["weight_policy"]
    assert policy["reference_vectors_remain_active"] is True
    assert policy["automatic_reweighting_forbidden"] is True
    assert policy["promotion_requires_point_in_time_out_of_sample_evidence"] is True
    assert policy["stress_period_optimization_weight"] == 0.0
    assert policy["v4_reweights_reference_scores"] is False


def test_v4_source_contract_replaces_investing_fail_closed():
    contract = json.loads(Path("config/WEEKLY_V4_SOURCE_CONTRACT.json").read_text(encoding="utf-8"))
    assert contract["investing"]["enabled"] is False
    assert contract["tradingview"]["replaces_investing"] is True
    assert contract["tradingview"]["free_name_search_forbidden"] is True
    assert contract["tradingview"]["exact_symbol_identity_proof_required"] is True
    assert contract["missing_data"]["negative_signal_imputation_forbidden"] is True


def test_etf_does_not_inherit_equity_analyst_contract():
    contract = json.loads(Path("config/WEEKLY_V4_SOURCE_CONTRACT.json").read_text(encoding="utf-8"))
    etf = contract["boursorama"]["etfs"]
    assert etf["analyst_consensus_required"] is False
    assert etf["analyst_count_required"] is False
    assert etf["target_upside_required"] is False
