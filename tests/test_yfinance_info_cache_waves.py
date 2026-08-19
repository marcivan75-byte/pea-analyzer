from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_action_and_etf_info_waves_use_persistent_cache_and_configured_ttl():
    source = (ROOT / "src" / "v182" / "reporting" / "waves.py").read_text(encoding="utf-8")
    assert "collect_info_cached" in source
    assert 'fundamental_refresh_days",7' in source
    assert 'YFINANCE_ACTION_INFO_V1.json' in source
    assert 'YFINANCE_ETF_INFO_V1.json' in source
    assert 'state"/"provenance"/"source_cache"' in source
    assert "YFINANCE_ACTION_INFO_V1_AUDIT.json" not in source  # generated from cache filename, no duplicated hard-code


def test_daily_workflow_persists_yfinance_source_cache_via_provenance_state():
    workflow = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    assert "state/provenance/" in workflow
    assert "Save provenance, V21.8 state and source caches" in workflow
