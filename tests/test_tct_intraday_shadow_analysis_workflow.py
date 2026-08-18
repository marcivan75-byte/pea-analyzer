from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v2421_analytics_is_non_blocking_and_separate_from_canonical_runtime():
    workflow = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    assert "TCT V24.2.1 shadow analytics" in workflow
    assert "python -m v182.reporting.tct_intraday_shadow_analysis" in workflow
    assert "outputs/audit/TCT_INTRADAY_V24_2_1_ANALYTICS.json" in workflow
    assert "outputs/mobile/ANDROID_TCT_INTRADAY_ANALYTICS.md" in workflow
    analytics_step = workflow.split("- name: TCT V24.2.1 shadow analytics", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error: true" in analytics_step

    canonical = (ROOT / "src" / "v182" / "reporting" / "daily_tct_ct_runner.py").read_text(encoding="utf-8")
    assert "tct_intraday_shadow_analysis" not in canonical
    assert "TCT_INTRADAY_V24_2_1_ANALYTICS" not in canonical
