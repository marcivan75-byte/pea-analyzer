from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from v182.audit.criteria_study_governance import (
    audit_action_registry,
    audit_etf_registry,
    audit_mt_high_precision,
    load_json,
)

ROOT=Path(__file__).resolve().parents[1]


def _configs():
    study=load_json(ROOT/"config"/"V21_6_3_CRITERIA_STUDY_GOVERNANCE.json")
    actions=load_json(ROOT/"config"/"V21_ACTIONS_CRITERIA_REGISTRY.json")
    etf=load_json(ROOT/"config"/"V20_7_1_ETF_CRITERIA_REGISTRY.json")
    mt=load_json(ROOT/"config"/"V20.8_ETF_MT_HIGH_PRECISION.json")
    return study,actions,etf,mt


def test_current_study_hardened_registries_pass():
    study,actions,etf,mt=_configs()
    assert audit_action_registry(actions,study)==[]
    assert audit_etf_registry(etf,study)==[]
    assert audit_mt_high_precision(mt,study)==[]


def test_derived_threshold_score_cannot_reenter_action_base_weight():
    study,actions,_,_=_configs()
    broken=deepcopy(actions)
    broken["weights"]["MT"]["target_upside_gt4_score"]=0.01
    findings=audit_action_registry(broken,study)
    assert any(f.code=="DERIVED_DOUBLE_COUNT" and f.criterion=="target_upside_gt4_score" for f in findings)


def test_overlay_cannot_reenter_action_base_weight():
    study,actions,_,_=_configs()
    broken=deepcopy(actions)
    broken["weights"]["CT"]["sector_rotation_score"]=0.01
    findings=audit_action_registry(broken,study)
    assert any(f.code=="OVERLAY_IN_BASE_ALPHA" and f.criterion=="sector_rotation_score" for f in findings)


def test_family_budget_mismatch_is_blocking():
    study,actions,_,_=_configs()
    broken=deepcopy(actions)
    broken["family_budgets"]["LT"]["FUNDAMENTALS"]+=0.01
    findings=audit_action_registry(broken,study)
    assert any(f.code in {"FAMILY_BUDGET_SUM_NOT_ONE","FAMILY_BUDGET_MISMATCH"} for f in findings)


def test_etf_derived_component_cannot_receive_weight():
    study,_,etf,_=_configs()
    broken=deepcopy(etf)
    broken["weights"]["CT"]["component_tracking_coverage"]=0.01
    findings=audit_etf_registry(broken,study)
    assert any(f.code=="FORBIDDEN_DERIVED_OR_CONTROL_WEIGHT" for f in findings)


def test_etf_model_counts_are_268_referential_43_target_composite_38_historical_subblock():
    study,_,_,mt=_configs()
    e=study["etf_study_hardening"]
    assert e["full_referential_criteria_count"]==268
    assert e["mt_target_composite_criteria_count"]==43
    assert e["mt_dynamic_pit_backtested_subblock_count"]==38
    assert e["mt_structural_target_count"]==5
    assert len(mt["dynamic_criteria"])==38
    assert len(mt["structural_overlay"])==5
