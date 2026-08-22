from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_daily_workflow_runs_collection_and_only_tactical_decision_runner():
    source = _text("committee_tct_ct_daily.yml")
    assert 'cron: "15 18 * * 1-5"' in source
    assert "PEA_RUN_PROFILE: DAILY_TACTICAL" in source
    assert "python -m v182.reporting.run" in source
    assert "python -m v182.reporting.daily_tct_ct_runner" in source
    assert "v182.reporting.unified_runner" not in source
    assert "etf_mt_v2081_run" not in source
    assert "sector_rotation" not in source.lower()
    assert "ipo_radar" not in source.lower()
    assert "beta_correlation_engine" not in source
    assert "retention-days: 7" in source


def test_heavy_committee_is_weekly_and_not_push_triggered():
    source = _text("committee_master_daily.yml")
    assert "name: PEA Weekly Heavy Committee V21.8.1" in source
    assert 'cron: "45 18 * * 5"' in source
    assert "push:" not in source.split("jobs:", 1)[0]
    assert "python -m v182.reporting.unified_runner" in source
    assert "python -m v182.reporting.criteria_governance_audit" in source
    assert "ANDROID_CI_CONTROL_CENTER.md" in source
    assert "retention-days: 14" in source


def test_standalone_etf_mt_is_manual_only():
    source = _text("etf_mt_v20_8_daily.yml")
    trigger_block = source.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger_block
    assert "schedule:" not in trigger_block
    assert "cron:" not in trigger_block


def test_fund_flows_schedule_is_owned_only_by_weekly_committee():
    standalone = _text("etf_fund_flows_daily.yml")
    trigger_block = standalone.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger_block
    assert "schedule:" not in trigger_block
    assert "cron:" not in trigger_block
    assert "run_validation:" in standalone
    assert "if: ${{ inputs.run_validation }}" in standalone

    weekly = _text("committee_master_daily.yml")
    assert 'cron: "45 18 * * 5"' in weekly
    assert "python -m v182.reporting.etf_fund_flows_shadow_run" in weekly
