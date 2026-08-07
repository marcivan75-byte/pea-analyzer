import pandas as pd


def test_stale_technical_value_refreshes_despite_row_level_b_evidence():
    from v182.io.frames import apply_observations

    master = pd.DataFrame([{
        "isin": "FR0000120073",
        "name": "AIR LIQUIDE",
        "rsi14": "40.0",
        "evidence_level": "B",
        "as_of_date": "2026-07-29",
        "ta_source": "Yahoo_chart",
        "ta_as_of": "2026-08-05T08:50:44+00:00",
    }])
    incoming = [{
        "universe": "ACTION", "isin": "FR0000120073", "field": "rsi14", "value": 55.0,
        "source": "INTERNAL_FROM_OHLCV_YFINANCE", "evidence_level": "C",
        "validation_status": "AUTO_MATCH", "as_of": "2026-08-07",
        "collected_at": "2026-08-07T08:00:00+00:00",
    }]
    updated, quarantine = apply_observations(master, incoming)
    assert quarantine == []
    assert updated.iloc[0]["rsi14"] == "55.0"
    assert "2026-08-07" in updated.iloc[0]["ta_as_of"]


def test_higher_evidence_official_fundamental_is_not_overwritten_by_yfinance():
    from v182.io.frames import apply_observations

    master = pd.DataFrame([{
        "isin": "FR0000120073", "name": "AIR LIQUIDE", "pb": "2.0",
        "evidence_level": "B", "as_of_date": "2026-07-29",
        "fundamentals_source": "Issuer official", "fundamentals_as_of": "2026-08-06",
    }])
    incoming = [{
        "universe": "ACTION", "isin": "FR0000120073", "field": "pb", "value": 2.2,
        "source": "yfinance", "evidence_level": "C", "validation_status": "AUTO_MATCH",
        "as_of": "2026-08-07", "collected_at": "2026-08-07T08:00:00+00:00",
    }]
    updated, quarantine = apply_observations(master, incoming)
    assert quarantine == []
    assert updated.iloc[0]["pb"] == "2.0"


def _frames():
    actions = pd.DataFrame([{"isin": f"FR{i:010d}", "yahoo_ticker": "X.PA"} for i in range(1486)])
    etf = pd.DataFrame([{"isin": f"LU{i:010d}", "yahoo_ticker": "Y.PA"} for i in range(102)])
    cov = {"ACTION": {"coverage_pct": 80.0}, "ETF": {"coverage_pct": 70.0}}
    cfg = {"quality_gates": {
        "actions_min_rows": 1486, "etf_min_rows": 102, "ticker_coverage_min_pct": 100.0,
        "ohlcv_success_min_pct": 90.0, "coverage_regression_tolerance_points": 0.0,
        "fundamentals_availability_min_pct": 40.0, "consensus_availability_min_pct": 40.0,
        "openfigi_transient_failure_max_pct": 25.0,
    }, "marketstack": {"max_symbols_per_run": 3, "max_new_symbol_resolutions_per_run": 1}}
    return actions, etf, cov, cfg


def test_quality_gate_blocks_broken_authenticated_openfigi_transport():
    from v182.audit.quality import run_quality_gates
    actions, etf, cov, cfg = _frames()
    metrics = {
        "WAVE_00_OPENFIGI": {"api_isins_requested": 100, "transient_failures": 100, "authenticated": True},
        "WAVE_01": {"requested": 100, "successful": 95},
        "WAVE_02": {"requested": 100, "successful": 95},
        "WAVE_04": {"requested": 300, "available": 200, "available_pct": 66.7},
        "WAVE_05": {"requested": 300, "available": 200, "available_pct": 66.7},
    }
    result = run_quality_gates(actions, etf, cov, cov, cfg, metrics)
    assert not result.passed
    assert any(c["check"] == "openfigi_transport_health" and not c["passed"] for c in result.checks)


def test_quality_gate_blocks_marketstack_path_not_invoked_when_needed(monkeypatch):
    from v182.audit.quality import run_quality_gates
    monkeypatch.delenv("MARKETSTACK_MAX_SYMBOLS_PER_RUN", raising=False)
    actions, etf, cov, cfg = _frames()
    diagnostics = {
        "remaining_after_openfigi": 10, "marketstack_key_present": True,
        "marketstack_symbol_resolution_attempted": 0, "marketstack_symbol_cache_hits": 0,
        "marketstack_attempted": 0, "marketstack_failures": [],
    }
    metrics = {
        "WAVE_00_OPENFIGI": {"api_isins_requested": 0, "transient_failures": 0, "authenticated": True},
        "WAVE_01": {"requested": 100, "successful": 95, "diagnostics": diagnostics},
        "WAVE_02": {"requested": 100, "successful": 95, "diagnostics": {}},
        "WAVE_04": {"requested": 300, "available": 200, "available_pct": 66.7},
        "WAVE_05": {"requested": 300, "available": 200, "available_pct": 66.7},
    }
    result = run_quality_gates(actions, etf, cov, cov, cfg, metrics)
    assert not result.passed
    assert any(c["check"] == "wave_01_marketstack_path_invoked_if_needed" and not c["passed"] for c in result.checks)


def test_quality_gate_accepts_legitimate_marketstack_identity_no_match(monkeypatch):
    from v182.audit.quality import run_quality_gates
    monkeypatch.delenv("MARKETSTACK_MAX_SYMBOLS_PER_RUN", raising=False)
    actions, etf, cov, cfg = _frames()
    diagnostics = {
        "remaining_after_openfigi": 10, "marketstack_key_present": True,
        "marketstack_symbol_resolution_attempted": 1,
        "marketstack_symbol_cache_hits": 0,
        "marketstack_symbol_failures": [{"reason": "NO_MATCH"}],
        "marketstack_attempted": 0, "marketstack_failures": [],
    }
    metrics = {
        "WAVE_00_OPENFIGI": {"api_isins_requested": 0, "transient_failures": 0, "authenticated": True},
        "WAVE_01": {"requested": 100, "successful": 95, "diagnostics": diagnostics},
        "WAVE_02": {"requested": 100, "successful": 95, "diagnostics": {}},
        "WAVE_04": {"requested": 300, "available": 200, "available_pct": 66.7},
        "WAVE_05": {"requested": 300, "available": 200, "available_pct": 66.7},
    }
    result = run_quality_gates(actions, etf, cov, cov, cfg, metrics)
    assert result.passed


def test_quality_gate_blocks_total_marketstack_resolver_transport_failure(monkeypatch):
    from v182.audit.quality import run_quality_gates
    monkeypatch.delenv("MARKETSTACK_MAX_SYMBOLS_PER_RUN", raising=False)
    actions, etf, cov, cfg = _frames()
    diagnostics = {
        "remaining_after_openfigi": 10, "marketstack_key_present": True,
        "marketstack_symbol_resolution_attempted": 1,
        "marketstack_symbol_failures": [{"reason": "HTTPError"}],
        "marketstack_symbol_cache_hits": 0,
        "marketstack_attempted": 0, "marketstack_failures": [],
    }
    metrics = {
        "WAVE_00_OPENFIGI": {"api_isins_requested": 0, "transient_failures": 0, "authenticated": True},
        "WAVE_01": {"requested": 100, "successful": 95, "diagnostics": diagnostics},
        "WAVE_02": {"requested": 100, "successful": 95, "diagnostics": {}},
        "WAVE_04": {"requested": 300, "available": 200, "available_pct": 66.7},
        "WAVE_05": {"requested": 300, "available": 200, "available_pct": 66.7},
    }
    result = run_quality_gates(actions, etf, cov, cov, cfg, metrics)
    assert not result.passed
    assert any(c["check"] == "wave_01_marketstack_resolver_transport_health" and not c["passed"] for c in result.checks)


def test_quality_gate_blocks_total_marketstack_eod_transport_failure(monkeypatch):
    from v182.audit.quality import run_quality_gates
    monkeypatch.delenv("MARKETSTACK_MAX_SYMBOLS_PER_RUN", raising=False)
    actions, etf, cov, cfg = _frames()
    diagnostics = {
        "remaining_after_openfigi": 10, "marketstack_key_present": True,
        "marketstack_symbol_resolution_attempted": 0, "marketstack_symbol_cache_hits": 3,
        "marketstack_attempted": 3,
        "marketstack_failures": [
            {"reason": "HTTPError"}, {"reason": "HTTPError"}, {"reason": "API_ERROR"},
        ],
    }
    metrics = {
        "WAVE_00_OPENFIGI": {"api_isins_requested": 0, "transient_failures": 0, "authenticated": True},
        "WAVE_01": {"requested": 100, "successful": 95, "diagnostics": diagnostics},
        "WAVE_02": {"requested": 100, "successful": 95, "diagnostics": {}},
        "WAVE_04": {"requested": 300, "available": 200, "available_pct": 66.7},
        "WAVE_05": {"requested": 300, "available": 200, "available_pct": 66.7},
    }
    result = run_quality_gates(actions, etf, cov, cov, cfg, metrics)
    assert not result.passed
    assert any(c["check"] == "wave_01_marketstack_eod_transport_health" and not c["passed"] for c in result.checks)
