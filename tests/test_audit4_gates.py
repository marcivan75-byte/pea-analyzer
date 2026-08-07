import json
from pathlib import Path

import pandas as pd


def _frames_and_cfg():
    actions = pd.DataFrame([{"isin": f"FR{i:010d}", "yahoo_ticker": "X.PA"} for i in range(1486)])
    etf = pd.DataFrame([{"isin": f"LU{i:010d}", "yahoo_ticker": "Y.PA"} for i in range(102)])
    cov = {"ACTION": {"coverage_pct": 80.0}, "ETF": {"coverage_pct": 70.0}}
    cfg = {
        "quality_gates": {
            "actions_min_rows": 1486,
            "etf_min_rows": 102,
            "ticker_coverage_min_pct": 100.0,
            "ohlcv_success_min_pct": 90.0,
            "coverage_regression_tolerance_points": 0.0,
            "fundamentals_availability_min_pct": 40.0,
            "consensus_availability_min_pct": 40.0,
            "openfigi_mapping_coverage_min_pct": 70.0,
            "openfigi_transient_failure_max_pct": 25.0,
        },
        "marketstack": {"max_symbols_per_run": 3, "max_new_symbol_resolutions_per_run": 1},
    }
    return actions, etf, cov, cfg


def _healthy_metrics(openfigi=None, wave01_diagnostics=None):
    return {
        "WAVE_00_OPENFIGI": openfigi or {
            "records": 100,
            "resolved": 80,
            "api_isins_requested": 100,
            "transient_failures": 0,
            "authenticated": True,
        },
        "WAVE_01": {
            "requested": 100,
            "successful": 95,
            "diagnostics": wave01_diagnostics or {},
        },
        "WAVE_02": {"requested": 100, "successful": 95, "diagnostics": {}},
        "WAVE_04": {"requested": 300, "available": 200, "available_pct": 66.7},
        "WAVE_05": {"requested": 300, "available": 200, "available_pct": 66.7},
    }


def test_deferred_only_marketstack_does_not_fake_activity(monkeypatch):
    from v182.audit.quality import run_quality_gates

    monkeypatch.delenv("MARKETSTACK_MAX_SYMBOLS_PER_RUN", raising=False)
    actions, etf, cov, cfg = _frames_and_cfg()
    diagnostics = {
        "remaining_after_openfigi": 10,
        "marketstack_key_present": True,
        "marketstack_symbol_resolution_attempted": 0,
        "marketstack_symbol_cache_hits": 0,
        "marketstack_symbol_negative_cache_hits": 0,
        "marketstack_symbol_deferred": 10,
        "marketstack_budget_exhausted": False,
        "marketstack_attempted": 0,
        "marketstack_failures": [],
    }
    result = run_quality_gates(actions, etf, cov, cov, cfg, _healthy_metrics(wave01_diagnostics=diagnostics))
    assert not result.passed
    assert any(
        check["check"] == "wave_01_marketstack_path_invoked_if_needed" and not check["passed"]
        for check in result.checks
    )


def test_openfigi_mapping_coverage_regression_blocks_release():
    from v182.audit.quality import run_quality_gates

    actions, etf, cov, cfg = _frames_and_cfg()
    openfigi = {
        "records": 100,
        "resolved": 50,
        "api_isins_requested": 100,
        "transient_failures": 0,
        "authenticated": True,
    }
    result = run_quality_gates(actions, etf, cov, cov, cfg, _healthy_metrics(openfigi=openfigi))
    assert not result.passed
    assert any(
        check["check"] == "openfigi_mapping_coverage_pct" and not check["passed"]
        for check in result.checks
    )


def test_production_quality_thresholds_are_not_permissive():
    cfg = json.loads(Path("config/V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    gates = cfg["quality_gates"]
    assert gates["fundamentals_availability_min_pct"] >= 90.0
    assert gates["consensus_availability_min_pct"] >= 90.0
    assert gates["openfigi_mapping_coverage_min_pct"] >= 70.0
