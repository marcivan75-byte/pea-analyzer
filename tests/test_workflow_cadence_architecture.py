from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_daily_workflow_runs_only_consolidated_tactical_runtime():
    source = _text("committee_tct_ct_daily.yml")
    facade = (ROOT / "src/v182/reporting/daily_consolidated_runner_v21_15_4.py").read_text(encoding="utf-8")
    impl = (ROOT / "src/v182/reporting/daily_consolidated_runner_v21_15_7.py").read_text(encoding="utf-8")

    assert 'cron: "0 21 * * 1-4"' in source
    assert "PEA_RUN_PROFILE: DAILY_TACTICAL" in source
    assert "PEA_SLOW_SOURCE_MODE: CACHE_PREFERRED" in source
    assert "python -m v182.reporting.daily_consolidated_runner_v21_15_4" in source
    assert source.count("python -m v182.reporting.daily_consolidated_runner_v21_15_4") == 1
    assert "daily_consolidated_runner_v21_15_7 as impl" in facade
    assert "daily_tactical_super_runner_v21_15_6 as tactical" in impl
    assert "committee_model_reruns\": 0" in impl
    assert "committee_external_collection_calls\": 0" in impl
    assert "sector_rotation" not in source.lower()
    assert "ipo_radar" not in source.lower()
    assert "beta_correlation_engine" not in source
    assert "retention-days: 7" in source
    assert "run_validation:" in source
    assert "if: ${{ inputs.run_validation }}" in source


def test_heavy_committee_is_weekly_and_not_push_triggered():
    source = _text("committee_master_daily.yml")
    tail = (ROOT / "src/v182/reporting/weekly_tail_super_runner_v21_16_0.py").read_text(encoding="utf-8")

    assert "name: PEA Weekly Heavy Committee V21.16.2" in source
    assert 'cron: "40 20 * * 5"' in source
    assert "PEA_SLOW_SOURCE_MODE: LIVE" in source
    assert "push:" not in source.split("jobs:", 1)[0]
    assert "python -m v182.reporting.weekly_unified_super_runner_v21_16_2" in source
    assert "python -m v182.reporting.weekly_tail_super_runner_v21_16_0" in source
    assert "friday_tactical_reuse_runner as friday_reuse" in tail
    assert "tactical_shadow_bundle_run as tactical" in tail
    assert "tct_postmarket_bundle_run as postmarket" in tail
    assert "weekly_post_decision_bundle_run as weekly_post" in tail
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
    tail = (ROOT / "src/v182/reporting/weekly_tail_super_runner_v21_16_0.py").read_text(encoding="utf-8")
    post = (ROOT / "src/v182/reporting/weekly_post_decision_bundle_run.py").read_text(encoding="utf-8")
    assert 'cron: "40 20 * * 5"' in weekly
    assert "weekly_post_decision_bundle_run as weekly_post" in tail
    assert "etf_fund_flows_shadow_run" in post


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


def test_postmarket_catalyst_is_consolidated_into_main_super_runners():
    catalyst = _text("tct_next_session_context.yml")
    daily = _text("committee_tct_ct_daily.yml")
    weekly = _text("committee_master_daily.yml")
    daily_v6 = (ROOT / "src/v182/reporting/daily_tactical_super_runner_v21_15_6.py").read_text(encoding="utf-8")
    daily_v5 = (ROOT / "src/v182/reporting/daily_tactical_super_runner_v21_15_5.py").read_text(encoding="utf-8")
    weekly_tail = (ROOT / "src/v182/reporting/weekly_tail_super_runner_v21_16_0.py").read_text(encoding="utf-8")
    bundle = (ROOT / "src/v182/reporting/tct_postmarket_bundle_run.py").read_text(encoding="utf-8")

    assert 'cron: "15 21 * * 1-5"' not in catalyst
    assert catalyst.count("cron:") == 1
    assert "python -m v182.reporting.daily_consolidated_runner_v21_15_4" in daily
    assert "daily_tactical_super_runner_v21_15_5 as base" in daily_v6
    assert "base.postmarket.run" in daily_v5
    assert "python -m v182.reporting.weekly_tail_super_runner_v21_16_0" in weekly
    assert "tct_postmarket_bundle_run as postmarket" in weekly_tail
    for workflow in (daily, weekly):
        assert "TCT_CATALYST_PHASE: POSTMARKET" not in workflow
        assert "python -m v182.reporting.tct_next_session_catalyst_run_v24_4_2" not in workflow
        assert "python -m v182.reporting.tct_v24_4_2_pit_lineage" not in workflow
        assert "python -m v182.reporting.tct_v24_4_2_pit_validator" not in workflow
    assert 'catalyst.run(root=root, phase="POSTMARKET")' in bundle
    assert "lineage.run(root=root)" in bundle
    assert "validator.run(root=root)" in bundle
