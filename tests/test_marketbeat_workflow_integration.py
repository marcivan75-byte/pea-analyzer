from pathlib import Path


def test_scheduled_workflow_executes_cached_priority_marketbeat_runner_and_finalizer():
    text = Path(".github/workflows/V18.2_online.yml").read_text(encoding="utf-8")
    assert "MARKETBEAT_API_KEY: ${{ secrets.MARKETBEAT_API_KEY }}" in text
    assert "python -m v182.decision.analyst_momentum" in text
    assert "python -m v182.decision.marketbeat_overlay_runner" in text
    assert "python -m v182.decision.committee_finalize" in text
    assert "config/V18.2_MARKETBEAT_SYMBOL_MAP.csv" in text
    assert "int(mb.get('successful', 0)) >= 1" in text
    assert "int(mb.get('api_calls', 0)) <= 5" in text


def test_full_audit_executes_live_schema_and_dynamic_marketbeat_crosscheck():
    text = Path(".github/workflows/V18.2_full_audit.yml").read_text(encoding="utf-8")
    assert "MarketBeat live forecast schema smoke" in text
    assert "client.get_stock_forecast('SNY', 'NASDAQ')" in text
    assert "MARKETBEAT_API_KEY: ${{ secrets.MARKETBEAT_API_KEY }}" in text
    assert "python -m v182.decision.marketbeat_overlay_runner" in text
    assert "python -m v182.decision.committee_finalize" in text
    assert "target_change_12m_abs" in text
    assert "marketbeat_risk_revision_pct" in text
    assert "config/V18.2_MARKETBEAT_SYMBOL_MAP.csv" in text
    assert "int(mb.get('successful', 0)) <= int(mb.get('selected', 0))" in text
    assert "int(mb.get('fatal_errors', 0)) == 0" in text
    assert "int(mb.get('api_calls', 0)) <= 5" in text
