from datetime import datetime, timezone
from unittest.mock import patch
import json
import pandas as pd


def test_yfinance_refresh_uses_yf_timestamp_not_legacy_ok_forever(monkeypatch):
    from v182.reporting import waves

    today = datetime.now(timezone.utc).isoformat()
    frame = pd.DataFrame([{
        "isin": "FR0000120073", "name": "AIR LIQUIDE", "yahoo_ticker": "AI.PA",
        "comite_status": "WATCH", "yf_status": "OK", "yf_consensus_as_of": today,
        "fundamentals_as_of": "2020-01-01", "per_ttm_yf": "25.0",
    }])
    called = []

    def fake_collect(tickers, **kwargs):
        called.extend(tickers)
        return [], []

    monkeypatch.setattr(waves, "collect_info", fake_collect)
    cfg = {"yfinance": {"fundamental_refresh_days": 7, "info_initial_cooldown_seconds": 0}}
    _, _, meta = waves.wave4_info_actions(frame, cfg, top_n=300)
    assert called == []
    assert meta["attempted"] == 0
    assert meta["skipped_fresh"] == 1


def test_lower_grade_yfinance_field_does_not_downgrade_official_fundamental_group():
    from v182.io.frames import apply_observations, FIELD_PROVENANCE_COLUMN

    frame = pd.DataFrame([{
        "isin": "FR0000120073", "name": "AIR LIQUIDE", "pb": "2.0", "per_ttm_yf": pd.NA,
        "fundamentals_source": "Issuer official", "fundamentals_as_of": "2026-08-06",
        "evidence_level": "A", "as_of_date": "2026-08-06", "yf_consensus_as_of": pd.NA,
    }])
    yf_pe = {
        "universe": "ACTION", "isin": "FR0000120073", "field": "per_ttm_yf", "value": 25.0,
        "source": "yfinance", "evidence_level": "C", "validation_status": "AUTO_MATCH",
        "as_of": "2026-08-07", "collected_at": "2026-08-07T08:00:00+00:00",
    }
    updated, _ = apply_observations(frame, [yf_pe])
    assert updated.iloc[0]["fundamentals_source"] == "Issuer official"
    assert updated.iloc[0]["fundamentals_as_of"] == "2026-08-06"
    provenance = json.loads(updated.iloc[0][FIELD_PROVENANCE_COLUMN])
    assert provenance["per_ttm_yf"]["evidence_level"] == "C"

    yf_pb = {**yf_pe, "field": "pb", "value": 2.4, "as_of": "2026-08-08"}
    protected, _ = apply_observations(updated, [yf_pb])
    assert protected.iloc[0]["pb"] == "2.0"


def test_openfigi_ticker_change_same_mic_forces_refresh(tmp_path, monkeypatch):
    from v182.mapping import etf_isin_resolver as resolver

    path = tmp_path / "openfigi.csv"
    pd.DataFrame([{
        "universe": "ACTION", "isin": "FR0000120578", "original_yahoo_ticker": "SAN.PA",
        "openfigi_ticker": "SAN", "openfigi_exch_code": "FP", "openfigi_mic": "XPAR",
        "yahoo_candidate": "SAN.PA", "figi": "OLD", "composite_figi": "", "share_class_figi": "",
        "status": "RESOLVED", "updated_at": datetime.now(timezone.utc).isoformat(),
    }]).to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    actions = pd.DataFrame([{"isin": "FR0000120578", "name": "Sanofi", "yahoo_ticker": "SASY.PA"}])
    etf = pd.DataFrame(columns=["isin", "name", "yahoo_ticker"])

    match = {"ticker": "SASY", "figi": "NEW", "marketSector": "Equity", "securityType2": "Common Stock"}
    monkeypatch.setattr(resolver, "resolve_isins", lambda isins, **kwargs: {"FR0000120578": [match]})
    summary = resolver.build_openfigi_master_map(actions, etf, path, api_key="fake")
    out = pd.read_csv(path, sep=";", dtype=str).fillna("")
    assert summary["api_isins_requested"] == 1
    assert summary["invalidated_identity_records"] == 1
    assert out.iloc[0]["original_yahoo_ticker"] == "SASY.PA"
    assert out.iloc[0]["figi"] == "NEW"


def test_openfigi_stale_identity_is_not_reused_after_transient_failure(tmp_path, monkeypatch):
    from v182.mapping import etf_isin_resolver as resolver

    path = tmp_path / "openfigi.csv"
    pd.DataFrame([{
        "universe": "ACTION", "isin": "FR0000120578", "original_yahoo_ticker": "SAN.PA",
        "openfigi_ticker": "SAN", "openfigi_exch_code": "FP", "openfigi_mic": "XPAR",
        "yahoo_candidate": "SAN.PA", "figi": "OLD", "composite_figi": "", "share_class_figi": "",
        "status": "RESOLVED", "updated_at": datetime.now(timezone.utc).isoformat(),
    }]).to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    actions = pd.DataFrame([{"isin": "FR0000120578", "name": "Sanofi", "yahoo_ticker": "SASY.PA"}])
    etf = pd.DataFrame(columns=["isin", "name", "yahoo_ticker"])
    monkeypatch.setattr(resolver, "resolve_isins", lambda isins, **kwargs: {"FR0000120578": None})
    summary = resolver.build_openfigi_master_map(actions, etf, path, api_key="fake")
    out = pd.read_csv(path, sep=";", dtype=str)
    assert summary["transient_failures"] == 1
    assert out.empty


def test_marketstack_negative_cache_counts_as_valid_path_activity(tmp_path):
    from v182.sources.marketstack_symbols import resolve_marketstack_symbols

    cache = tmp_path / "marketstack.csv"
    pd.DataFrame([{
        "universe": "ACTION", "isin": "FR0000120578", "name": "Sanofi",
        "original_yahoo_ticker": "SAN.PA", "expected_mic": "XPAR", "marketstack_symbol": "",
        "matched_name": "", "confidence": "", "status": "NO_MATCH",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }]).to_csv(cache, sep=";", index=False, encoding="utf-8-sig")
    rows = pd.DataFrame([{"isin": "FR0000120578", "name": "Sanofi", "yahoo_ticker": "SAN.PA"}])
    with patch("v182.sources.marketstack_symbols.resolve_one") as resolve:
        result = resolve_marketstack_symbols(rows, "ACTION", cache, "fake", delay_seconds=0)
    resolve.assert_not_called()
    assert result.api_attempted == 0
    assert result.negative_cache_hits == 1
    assert result.failures[0]["cached"] is True


def test_marketstack_quality_gate_accepts_fresh_negative_cache(monkeypatch):
    from v182.audit.quality import run_quality_gates

    monkeypatch.delenv("MARKETSTACK_MAX_SYMBOLS_PER_RUN", raising=False)
    actions = pd.DataFrame([{"isin": f"FR{i:010d}", "yahoo_ticker": "X.PA"} for i in range(1486)])
    etf = pd.DataFrame([{"isin": f"LU{i:010d}", "yahoo_ticker": "Y.PA"} for i in range(102)])
    cov = {"ACTION": {"coverage_pct": 80.0}, "ETF": {"coverage_pct": 70.0}}
    cfg = {"quality_gates": {
        "actions_min_rows": 1486, "etf_min_rows": 102, "ticker_coverage_min_pct": 100.0,
        "ohlcv_success_min_pct": 90.0, "coverage_regression_tolerance_points": 0.0,
        "fundamentals_availability_min_pct": 40.0, "consensus_availability_min_pct": 40.0,
        "openfigi_transient_failure_max_pct": 25.0,
    }, "marketstack": {"max_symbols_per_run": 3, "max_new_symbol_resolutions_per_run": 1}}
    diagnostics = {
        "remaining_after_openfigi": 1, "marketstack_key_present": True,
        "marketstack_symbol_resolution_attempted": 0, "marketstack_symbol_cache_hits": 0,
        "marketstack_symbol_negative_cache_hits": 1, "marketstack_symbol_deferred": 0,
        "marketstack_symbol_failures": [{"reason": "NO_MATCH", "cached": True}],
        "marketstack_attempted": 0, "marketstack_failures": [],
    }
    metrics = {
        "WAVE_00_OPENFIGI": {"api_isins_requested": 0, "transient_failures": 0, "authenticated": True},
        "WAVE_01": {"requested": 100, "successful": 95, "diagnostics": diagnostics},
        "WAVE_02": {"requested": 100, "successful": 95, "diagnostics": {}},
        "WAVE_04": {"requested": 300, "available": 200, "available_pct": 66.7},
        "WAVE_05": {"requested": 300, "available": 200, "available_pct": 66.7},
    }
    assert run_quality_gates(actions, etf, cov, cov, cfg, metrics).passed


def test_finnhub_lookup_rejects_weak_match_and_accepts_strong_isin_match():
    from v182.sources.finnhub_consensus import _pick_lookup_result

    weak = [
        {"symbol": "XYZ.US", "displaySymbol": "XYZ", "type": "Common Stock", "description": "Unrelated Corp"},
        {"symbol": "ABC.US", "displaySymbol": "ABC", "type": "Common Stock", "description": "Another Corp"},
    ]
    assert _pick_lookup_result(weak, "SAN.PA", name="Sanofi", queried_by_isin=False) is None

    strong = [{"symbol": "SAN.PA", "displaySymbol": "SAN.PA", "type": "Common Stock", "description": "Sanofi"}]
    picked = _pick_lookup_result(strong, "SAN.PA", name="Sanofi", queried_by_isin=True)
    assert picked["symbol"] == "SAN.PA"
