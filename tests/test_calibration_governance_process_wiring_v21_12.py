from pathlib import Path


def test_weekly_committee_executes_governance_audit_with_calibration_gate():
    workflow = Path(".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    tail = Path("src/v182/reporting/weekly_tail_super_runner_v21_16_0.py").read_text(encoding="utf-8")
    post_bundle = Path("src/v182/reporting/weekly_post_decision_bundle_run.py").read_text(encoding="utf-8")
    governance_module = Path("src/v182/reporting/criteria_governance_audit.py").read_text(encoding="utf-8")

    assert "python -m v182.reporting.weekly_operational_runner_v4_4" in workflow
    assert "weekly_post_decision_bundle_run as weekly_post" in tail
    assert "criteria_governance_audit" in post_bundle
    assert "calibration_governance_audit.run(root)" in governance_module
    assert "CALIBRATION_GOVERNANCE_V21_12.json" in Path(
        "src/v182/reporting/calibration_governance_audit.py"
    ).read_text(encoding="utf-8")


def test_sector_rotation_module_specific_protocol_is_not_replaced_by_generic_filter():
    source = Path("src/v182/backtest/sector_rotation_v2_pit_oos.py").read_text(encoding="utf-8")
    assert "calibration_windows" not in source
