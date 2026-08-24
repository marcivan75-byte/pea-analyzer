from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/weekly_v4_validation.yml"


def test_v4_workflow_is_least_privilege_bounded_and_pinned():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "contents: read" in text
    assert "actions: read" in text
    assert "timeout-minutes: 30" in text
    assert "cancel-in-progress: true" in text
    assert "BASELINE_ARTIFACT_ID" in text
    assert "= \"15\"" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- uses:"):
            reference = stripped.split("@", 1)[1]
            assert len(reference) == 40
            assert all(ch in "0123456789abcdef" for ch in reference)


def test_v4_workflow_runs_all_quality_layers_and_disables_investing():
    text = WORKFLOW.read_text(encoding="utf-8")
    for required in (
        "ruff check src tests",
        "pytest -q",
        "weekly_v4_governance",
        "weekly_v4_calibration",
        "audit_v4_sources_live.py",
        "v182.reporting.ci_light_v4",
        "audit['investing_enabled'] is False",
    ):
        assert required in text
