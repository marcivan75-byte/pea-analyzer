from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "src/v182/reporting/weekly_operational_runner_v4_4.py"
WORKFLOW = ROOT / ".github/workflows/committee_master_daily.yml"
GOVERNANCE = ROOT / "config/WEEKLY_V4_GOVERNANCE.json"


def test_runner_is_orchestration_only():
    text = RUNNER.read_text(encoding="utf-8")
    assert "WEEKLY_OPERATIONAL_V4_4_UNIFIED" in text
    assert "PEA_WEEKLY_CRITICAL_ONLY" in text
    assert "CACHE_PREFERRED" in text
    assert "or_ranking_daily_shadow_v1" in text
    assert "objectives_risk_reference_influence" in text
    assert "real_orders_enabled" in text
    assert "criteria_changed\": False" in text
    assert "weights_changed\": False" in text
    assert "thresholds_changed\": False" in text


def test_workflow_points_to_v4_4_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "name: PEA Weekly Heavy Committee V4.4" in text
    assert "python -m v182.reporting.weekly_operational_runner_v4_4" in text
    assert "committee-weekly-v4-4-" in text
    assert "PEA_SLOW_SOURCE_MODE:" in text
    assert "PEA_WEEKLY_CRITICAL_ONLY: \"1\"" in text
    assert "maintenance_full_refresh:" in text
    assert "WEEKLY_OPERATIONAL_RUNTIME_V4_4.json" in text
    assert "OBJECTIVES_RISK_CHALLENGER_V2.json" in text


def test_governance_keeps_selection_frozen_and_exposes_v4_4_orchestration():
    import json
    cfg = json.loads(GOVERNANCE.read_text(encoding="utf-8"))
    assert cfg["selection"]["minimum_selection_score"] == 77.0
    assert cfg["selection"]["minimum_confidence_score"] == 66.0
    assert cfg["weight_policy"]["v4_reweights_reference_scores"] is False
    assert cfg["release"]["real_orders_enabled"] is False
    orch = cfg["operational_orchestration"]
    assert orch["runner_version"] == "WEEKLY_OPERATIONAL_V4_4_UNIFIED"
    assert orch["slow_source_mode_default"] == "CACHE_PREFERRED"
    assert orch["objectives_risk_decision_influence"] == 0.0
    assert orch["artifact_name_prefix"] == "committee-weekly-v4-4"
