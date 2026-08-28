from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_report_module_writes_latest_aliases_and_markdown():
    text = (ROOT / "src/v182/reporting/or_hebdo_report_v1.py").read_text(encoding="utf-8")
    assert "OR_RANKING_HEBDO_SHADOW_LATEST.csv" in text
    assert "OR_HEBDO_REPORT.md" in text
    assert "ANDROID_OR_HEBDO_SHADOW.md" in text
    assert "real_orders_enabled" in text
    assert "score_influence" in text


def test_runner_calls_automated_or_report():
    text = (ROOT / "src/v182/reporting/weekly_operational_runner_v4_4.py").read_text(encoding="utf-8")
    assert "or_hebdo_report_v1" in text
    assert "or_hebdo_report_status" in text


def test_workflow_artifact_is_v4_4_and_publishes_or_reports():
    text = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    assert "committee-weekly-v4-4-" in text
    assert "committee-weekly-v21-16-2-" not in text
    assert "OR_HEBDO_REPORT.md" in text
    assert "ANDROID_OR_HEBDO_SHADOW.md" in text
    assert "state/objectives_risk/" in text
    assert "Upload complete weekly Committee V4.4 results" in text
