from pathlib import Path

from v182.reporting import unified_runner


def test_unified_runner_wires_canonical_ci_explainability():
    source = Path(unified_runner.__file__).read_text(encoding="utf-8")
    assert "committee_ci_explainability" in source
    assert 'steps["ci_explainability"]' in source
    assert 'committee_ci_explainability.run(root)' in source
    assert '"ci_android": "outputs/mobile/ANDROID_CI_CONTROL_CENTER.md"' in source
    assert '"ci_pc": "outputs/committee_master/CI_PC_EXPLAINABILITY.xlsx"' in source
    assert '"ci_explainability_audit": "outputs/audit/CI_EXPLAINABILITY_AUDIT.json"' in source


def test_ci_reporting_remains_read_only_governance():
    source = Path(unified_runner.__file__).read_text(encoding="utf-8")
    assert "cannot mutate scores, decisions, weights, thresholds or order state" in source
    assert '"live_orders_enabled": False' in source
    assert '"virtual_performance": "SKIPPED_GOVERNANCE' in source
