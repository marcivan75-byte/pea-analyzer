from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tct_intraday_shadow_is_non_blocking_and_persisted():
    workflow = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    assert "TCT V24.2 intraday scalping shadow" in workflow
    assert "continue-on-error: true" in workflow
    assert "python -m v182.reporting.tct_intraday_shadow_run" in workflow
    assert "state/TCT_V24_2_0_SIGNAL_LEDGER.csv" in workflow
    assert "state/TCT_V24_2_0_INTRADAY_OBSERVATIONS.csv" in workflow
    assert "outputs/audit/TCT_INTRADAY_V24_2_0_AUDIT.json" in workflow
    assert "path: data/cache/" in workflow
    assert "find data/cache/actions_intraday_5m -maxdepth 2 -name history_manifest.json" in workflow
    assert "data/cache/actions_intraday_5m/history_manifest.json" not in workflow


def test_tct_intraday_runner_enforces_post_signal_completed_sessions_only():
    source = (ROOT / "src" / "v182" / "reporting" / "tct_intraday_shadow_run.py").read_text(encoding="utf-8")
    assert "def _eligible_completed_sessions" in source
    assert "minimum_lag = max(1" in source
    assert "current_calendar_session_persistence_forbidden" in source
    assert "future = [s for s in future if s < current]" in source
    assert 'if result.status == "SESSION_INCOMPLETE"' in source
    assert '"completed_sessions_only":' in source
    assert '"decision_influence": 0.0' in source
    assert '"score_influence": 0.0' in source
    assert '"sizing_execution_influence": 0.0' in source
    assert '"stop_loss_influence": 0.0' in source
    assert '"real_orders_enabled": False' in source


def test_tct_intraday_shadow_is_not_wired_into_canonical_adapter():
    daily = (ROOT / "src" / "v182" / "reporting" / "daily_tct_ct_runner.py").read_text(encoding="utf-8")
    assert "tct_intraday_shadow" not in daily
    assert "TCT_INTRADAY_V24_2_0_SHADOW" not in daily
