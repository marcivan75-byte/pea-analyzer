from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v2441_context_workflow_runs_only_two_lightweight_snapshots():
    workflow = (ROOT / ".github" / "workflows" / "tct_next_session_context.yml").read_text(encoding="utf-8")
    assert 'cron: "40 6 * * 1-5"' in workflow
    assert 'cron: "15 21 * * 1-5"' in workflow
    assert "python -m v182.reporting.tct_next_session_catalyst_run_v24_4_1" in workflow
    assert "python -m v182.reporting.run" not in workflow
    assert "daily_tct_ct_runner" not in workflow
    assert "download_history" not in workflow
    assert "1m" not in workflow
    assert "5m" not in workflow
    assert "state/tct_context/" in workflow
    assert "TCT_V24_4_1_CATALYST_LEDGER.csv" in workflow
    assert "TCT_V24_4_0_CATALYST_LEDGER.csv" not in workflow


def test_daily_workflow_persists_context_seed_but_does_not_run_v2441_continuously():
    workflow = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    assert "Restore next-session catalyst state" in workflow
    assert "Save next-session catalyst state" in workflow
    assert "state/tct_context/" in workflow
    assert "TCT_DAILY_TRADER_LATEST.csv" in workflow
    assert "tct_next_session_catalyst_run" not in workflow


def test_v2441_runner_preserves_no_extended_hours_or_intraday_authority():
    base_source = (ROOT / "src" / "v182" / "reporting" / "tct_next_session_catalyst_run.py").read_text(encoding="utf-8")
    wrapper = (ROOT / "src" / "v182" / "reporting" / "tct_next_session_catalyst_run_v24_4_1.py").read_text(encoding="utf-8")
    assert '"individual_pea_extended_hours_quotes_used": False' in base_source
    assert '"intraday_bars_used": False' in base_source
    assert '"five_minute_data_used": False' in base_source
    assert '"continuous_monitoring_used": False' in base_source
    assert '"snapshot_count_design_per_day": 2' in base_source
    assert '"decision_influence": 0.0' in base_source
    assert '"score_influence": 0.0' in base_source
    assert '"sizing_influence": 0.0' in base_source
    assert '"stop_loss_influence": 0.0' in base_source
    assert '"ct_influence": 0.0' in base_source
    assert "TCT_V24_4_1_CATALYST_CONTEXT_SHADOW.json" in wrapper


def test_v2431_seed_reuses_already_collected_context_fields():
    source = (ROOT / "src" / "v182" / "reporting" / "tct_daily_trader_shadow_run_v24_3_1.py").read_text(encoding="utf-8")
    assert "TCT_DAILY_TRADER_LATEST.csv" in source
    assert "days_to_earnings" in source
    assert "news_catalyst_score" in source
    assert "funnel_instrument_news_score" in source
    assert "market_cap" in source
    assert "download_history" not in source
