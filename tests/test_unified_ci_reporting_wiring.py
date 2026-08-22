from pathlib import Path

from v182.reporting import unified_runner


ROOT = Path(__file__).resolve().parents[1]


def test_unified_runner_wires_v21_16_canonical_ci_explainability():
    source = Path(unified_runner.__file__).read_text(encoding="utf-8")
    assert "committee_ci_explainability_v21_16" in source
    assert 'steps["ci_explainability"]' in source
    assert 'committee_ci_explainability_v21_16.run(root)' in source
    assert '"ci_android": "outputs/mobile/ANDROID_CI_CONTROL_CENTER.md"' in source
    assert '"ci_word": "outputs/committee_master/CI_COMITE_INVESTISSEMENT.docx"' in source
    assert '"ci_weighted_reference": "outputs/committee_master/CI_REFERENTIEL_PONDERE.xlsx"' in source
    assert '"ci_source_validation": "outputs/committee_master/CI_VALIDATION_SOURCES.csv"' in source
    assert '"ci_decision_brief_v3": "outputs/decision_brief/CI_DECISION_BRIEF_V3.docx"' in source
    assert '"ci_decision_matrix_v3": "outputs/decision_brief/CI_DECISION_MATRIX_V3.csv"' in source
    assert '"ci_recommendation_basket_risk": "outputs/committee_master/CI_RISQUE_PANIER_RECOMMANDATIONS.docx"' in source
    assert '"ci_explainability_audit": "outputs/audit/CI_EXPLAINABILITY_AUDIT.json"' in source
    assert "CI_PC_EXPLAINABILITY.xlsx" not in source


def test_ci_reporting_remains_read_only_governance():
    source = Path(unified_runner.__file__).read_text(encoding="utf-8")
    assert "cannot mutate scores, decisions, weights, thresholds or order state" in source
    assert '"live_orders_enabled": False' in source
    assert '"virtual_performance": "SKIPPED_GOVERNANCE' in source
    assert "TCT Daily / CT Weekly / MT Monthly" in source


def test_weekly_workflow_uses_source_gated_decision_brief():
    workflow = (ROOT / ".github" / "workflows" / "committee_master_daily.yml").read_text(encoding="utf-8")
    assert "python -m v182.reporting.decision_brief_v21_16" in workflow
    assert "python -m v182.reporting.decision_brief\n" not in workflow
