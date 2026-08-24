from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v2431_runner_requires_no_new_market_download():
    source = (ROOT / "src" / "v182" / "reporting" / "tct_daily_trader_shadow_run_v24_3_1.py").read_text(encoding="utf-8")
    assert "download_history" not in source
    assert "actions_intraday_5m" not in source
    assert '"intraday_data_used": False' in source
    assert '"five_minute_data_used": False' in source
    assert '"quasi_realtime_data_used": False' in source
    assert '"new_market_data_downloads_required": False' in source
    assert '"decision_influence": 0.0' in source
    assert '"score_influence": 0.0' in source
    assert '"sizing_influence": 0.0' in source
    assert '"stop_loss_influence": 0.0' in source
    assert '"ct_influence": 0.0' in source
    assert "_completed_daily_history" in source


def test_daily_workflow_runs_v2431_via_consolidated_tactical_dag_and_purges_old_cache():
    workflow = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    daily_entry = (ROOT / "src" / "v182" / "reporting" / "daily_consolidated_runner_v21_15_4.py").read_text(encoding="utf-8")
    daily_impl = (ROOT / "src" / "v182" / "reporting" / "daily_consolidated_runner_v21_15_7.py").read_text(encoding="utf-8")
    daily_tactical = (ROOT / "src" / "v182" / "reporting" / "daily_tactical_super_runner_v21_15_4.py").read_text(encoding="utf-8")
    bundle = (ROOT / "src" / "v182" / "reporting" / "tactical_shadow_bundle_run.py").read_text(encoding="utf-8")

    assert "python -m v182.reporting.daily_consolidated_runner_v21_15_4" in workflow
    assert "daily_consolidated_runner_v21_15_7 as impl" in daily_entry
    assert "daily_tactical_super_runner_v21_15_6 as tactical" in daily_impl
    assert "tactical_shadow_bundle_run as tactical" in daily_tactical
    assert "tct_trader.run(root=root)" in bundle
    assert "ANDROID_TCT_DAILY_TRADER_SHADOW.md" in workflow
    assert "TCT_DAILY_TRADER_V24_3_1_AUDIT.json" in workflow
    assert "rm -rf data/cache/actions_intraday_5m" in workflow
    assert "tct_intraday_shadow_run" not in workflow
    assert "tct_intraday_shadow_analysis" not in workflow
    assert "TCT_V24_2_0_SIGNAL_LEDGER" not in workflow
    assert "TCT_V24_2_0_INTRADAY_OBSERVATIONS" not in workflow


def test_v2431_does_not_modify_canonical_tct_ct_runner():
    canonical = (ROOT / "src" / "v182" / "reporting" / "daily_tct_ct_runner.py").read_text(encoding="utf-8")
    assert "tct_daily_trader" not in canonical
    assert "V24_3" not in canonical


def test_obsolete_v242_runtime_files_are_removed():
    obsolete = [
        ROOT / "config" / "TCT_V24_2_0_INTRADAY_SHADOW.json",
        ROOT / "src" / "v182" / "features" / "tct_intraday_v24_2.py",
        ROOT / "src" / "v182" / "decision" / "tct_intraday_shadow_v24_2.py",
        ROOT / "src" / "v182" / "reporting" / "tct_intraday_shadow_run.py",
        ROOT / "src" / "v182" / "reporting" / "tct_intraday_shadow_analysis.py",
    ]
    assert all(not path.exists() for path in obsolete)
