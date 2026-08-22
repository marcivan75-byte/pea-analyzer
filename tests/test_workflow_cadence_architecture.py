from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_daily_workflow_runs_collection_and_only_tactical_decision_runner():
    source = _text("committee_tct_ct_daily.yml")
    assert 'cron: "0 21 * * 1-4"' in source
    assert "PEA_RUN_PROFILE: DAILY_TACTICAL" in source
    assert "PEA_SLOW_SOURCE_MODE: CACHE_PREFERRED" in source
    assert "python -m v182.reporting.run" in source
    assert "python -m v182.reporting.daily_tct_ct_runner" in source
    assert source.count("python -m v182.reporting.tactical_shadow_bundle_run") == 1
    assert source.count("python -m v182.reporting.tct_postmarket_bundle_run") == 1
    assert "python -m v182.reporting.action_ct_shadow_bundle_run" not in source
    assert "python -m v182.reporting.tct_daily_trader_shadow_run_v24_3_1" not in source
    assert "v182.reporting.unified_runner" not in source
    assert "etf_mt_v2081_run" not in source
    assert "sector_rotation" not in source.lower()
    assert "ipo_radar" not in source.lower()
    assert "beta_correlation_engine" not in source
    assert "retention-days: 7" in source
    assert "run_validation:" in source
    assert "if: ${{ inputs.run_validation }}" in source


def test_heavy_committee_is_weekly_and_not_push_triggered():
    source = _text("committee_master_daily.yml")
    assert "name: PEA Weekly Heavy Committee V21.8.1" in source
    assert 'cron: "40 20 * * 5"' in source
    assert "PEA_SLOW_SOURCE_MODE: LIVE" in source
    assert "push:" not in source.split("jobs:", 1)[0]
    assert "python -m v182.reporting.unified_runner" in source
    assert "python -m v182.reporting.criteria_governance_audit" in source
    assert "python -m v182.reporting.daily_tct_ct_runner" in source
    assert source.count("python -m v182.reporting.tactical_shadow_bundle_run") == 1
    assert source.count("python -m v182.reporting.tct_postmarket_bundle_run") == 1
    assert "python -m v182.reporting.action_ct_shadow_bundle_run" not in source
    assert "python -m v182.reporting.action_ct_shadow_run_v22_0" not in source
    assert "python -m v182.reporting.action_ct_shadow_run_v22_1" not in source
    assert "python -m v182.reporting.tct_daily_trader_shadow_run_v24_3_1" not in source
    assert "python -m v182.reporting.tct_pit_ohlc_ledger_v24_4_2" not in source
    assert "state/tct_context/" in source
    assert "state/action_ct/" in source
    assert "state/action_ct_v22_1/" in source
    assert "run_validation:" in source
    assert source.count("if: ${{ inputs.run_validation }}") >= 2
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
    assert 'cron: "40 20 * * 5"' in weekly
    assert "python -m v182.reporting.etf_fund_flows_shadow_run" in weekly


def test_scheduled_runtime_validation_is_opt_in_for_catalyst_snapshots():
    catalyst = _text("tct_next_session_context.yml")
    assert "run_validation:" in catalyst
    compile_step = catalyst.split("- name: Compile V24.4.2 catalyst runtime", 1)[1].split("- name:", 1)[0]
    assert "if: ${{ inputs.run_validation }}" in compile_step
    manual_only = "if: ${{ github.event_name == 'workflow_dispatch' }}"
    lineage_step = catalyst.split("- name: Apply fail-closed V24.4.2 PIT OHLC lineage", 1)[1].split("- name:", 1)[0]
    validator_step = catalyst.split("- name: Validate accumulated V24.4.2 PIT ledger", 1)[1].split("- name:", 1)[0]
    assert manual_only in lineage_step
    assert manual_only in validator_step


def test_postmarket_catalyst_is_consolidated_into_existing_main_jobs():
    catalyst = _text("tct_next_session_context.yml")
    daily = _text("committee_tct_ct_daily.yml")
    weekly = _text("committee_master_daily.yml")
    bundle = (ROOT / "src/v182/reporting/tct_postmarket_bundle_run.py").read_text(encoding="utf-8")
    assert 'cron: "15 21 * * 1-5"' not in catalyst
    assert catalyst.count("cron:")==1
    for workflow in (daily,weekly):
        assert workflow.count("python -m v182.reporting.tct_postmarket_bundle_run") == 1
        assert "TCT_CATALYST_PHASE: POSTMARKET" not in workflow
        assert "python -m v182.reporting.tct_next_session_catalyst_run_v24_4_2" not in workflow
        assert "python -m v182.reporting.tct_v24_4_2_pit_lineage" not in workflow
        assert "python -m v182.reporting.tct_v24_4_2_pit_validator" not in workflow
    assert 'catalyst.run(root=root, phase="POSTMARKET")' in bundle
    assert "lineage.run(root=root)" in bundle
    assert "validator.run(root=root)" in bundle
