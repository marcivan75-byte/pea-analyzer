from unittest.mock import MagicMock, patch
import json
import pandas as pd
import numpy as np


def test_pipeline_modules_and_config_load():
    import v182.reporting.run  # noqa: F401
    import v182.sources.history_orchestrator  # noqa: F401
    import v182.sources.marketstack_eod  # noqa: F401
    cfg = json.load(open("config/V18.2_MASTER_CONFIG.json", encoding="utf-8"))
    assert cfg["openfigi"]["api_version"] == "v3"
    assert cfg["openfigi"]["mapping_strategy"] == "ISIN_PLUS_EXPECTED_MIC"
    assert cfg["marketstack"]["enabled"] is True
    assert cfg["marketstack"]["require_exact_exchange_mic"] is True


def test_yfinance_empty_columns_are_not_success():
    from v182.sources.yfinance_bulk import _ticker_has_data

    idx = pd.date_range("2026-01-01", periods=80, freq="B")
    columns = pd.MultiIndex.from_product([["GOOD.PA", "EMPTY.PA"], ["Open", "High", "Low", "Close", "Volume"]])
    frame = pd.DataFrame(index=idx, columns=columns, dtype=float)
    frame[("GOOD.PA", "Close")] = np.linspace(100, 120, len(idx))
    frame[("GOOD.PA", "Open")] = frame[("GOOD.PA", "Close")]
    frame[("GOOD.PA", "High")] = frame[("GOOD.PA", "Close")] + 1
    frame[("GOOD.PA", "Low")] = frame[("GOOD.PA", "Close")] - 1
    frame[("GOOD.PA", "Volume")] = 1000

    assert _ticker_has_data(frame, "GOOD.PA", min_rows=20) is True
    assert _ticker_has_data(frame, "EMPTY.PA", min_rows=20) is False


def test_openfigi_master_map_uses_expected_mic_not_bloomberg_exchange_code(tmp_path):
    from v182.mapping.etf_isin_resolver import build_openfigi_master_map

    actions = pd.DataFrame([{"isin": "FR0000120073", "yahoo_ticker": "AI.PA"}])
    etfs = pd.DataFrame(columns=["isin", "yahoo_ticker"])
    fake = {
        "FR0000120073": [{
            "figi": "BBGTEST",
            "compositeFIGI": "BBGCOMP",
            "shareClassFIGI": "BBGSHARE",
            "ticker": "AIR",
            "exchCode": "FP",
            "marketSector": "Equity",
            "securityType2": "Common Stock",
        }]
    }
    output = tmp_path / "openfigi.csv"
    with patch("v182.mapping.etf_isin_resolver.resolve_isins", return_value=fake) as resolve:
        summary = build_openfigi_master_map(actions, etfs, output, api_key="fake")

    mapped = pd.read_csv(output, sep=";", dtype=str).fillna("")
    assert summary["resolved"] == 1
    assert mapped.iloc[0]["yahoo_candidate"] == "AIR.PA"
    assert mapped.iloc[0]["openfigi_mic"] == "XPAR"
    assert mapped.iloc[0]["openfigi_exch_code"] == "FP"
    assert mapped.iloc[0]["figi"] == "BBGTEST"
    assert resolve.call_args.kwargs["mic_by_isin"]["FR0000120073"] == "XPAR"


def test_openfigi_transient_failure_is_not_negative_cached(tmp_path):
    from v182.mapping.etf_isin_resolver import build_openfigi_master_map

    actions = pd.DataFrame([{"isin": "FR0000120073", "yahoo_ticker": "AI.PA"}])
    etfs = pd.DataFrame(columns=["isin", "yahoo_ticker"])
    output = tmp_path / "openfigi.csv"

    with patch("v182.mapping.etf_isin_resolver.resolve_isins", return_value={"FR0000120073": None}):
        first = build_openfigi_master_map(actions, etfs, output, api_key="fake")
    assert first["transient_failures"] == 1
    assert first["records"] == 0

    fake_ok = {"FR0000120073": [{
        "figi": "BBGTEST", "ticker": "AIR", "exchCode": "FP",
        "marketSector": "Equity", "securityType2": "Common Stock",
    }]}
    with patch("v182.mapping.etf_isin_resolver.resolve_isins", return_value=fake_ok) as retry:
        second = build_openfigi_master_map(actions, etfs, output, api_key="fake")
    assert retry.called
    assert second["resolved"] == 1


def test_openfigi_pick_best_match_rejects_derivative():
    from v182.mapping.etf_isin_resolver import pick_best_match

    matches = [
        {"ticker": "AI 8 C200", "marketSector": "Equity", "securityType2": "Option"},
        {"ticker": "AIR", "marketSector": "Equity", "securityType2": "Common Stock"},
    ]
    assert pick_best_match(matches)["ticker"] == "AIR"


def test_fallback_specs_can_use_original_symbol_and_mic_without_openfigi(tmp_path):
    from v182.mapping.etf_isin_resolver import fallback_specs

    master = pd.DataFrame([{"isin": "FR0000120073", "yahoo_ticker": "AI.PA"}])
    specs = fallback_specs(master, ["AI.PA"], tmp_path / "missing.csv", "ACTION")
    assert specs["AI.PA"]["marketstack_symbol"] == "AI"
    assert specs["AI.PA"]["marketstack_mic"] == "XPAR"
    assert specs["AI.PA"]["yahoo_candidate"] == ""


def test_marketstack_eod_is_converted_to_indicator_frame():
    from v182.sources.marketstack_eod import fetch_eod_history

    rows = []
    for i, date in enumerate(pd.date_range("2026-01-01", periods=80, freq="B")):
        rows.append({
            "symbol": "AIR",
            "exchange": "XPAR",
            "date": date.strftime("%Y-%m-%dT00:00:00+0000"),
            "open": 100 + i,
            "high": 101 + i,
            "low": 99 + i,
            "close": 100.5 + i,
            "volume": 100000 + i,
            "adj_open": 100 + i,
            "adj_high": 101 + i,
            "adj_low": 99 + i,
            "adj_close": 100.5 + i,
            "adj_volume": 100000 + i,
        })
    response = MagicMock()
    response.raise_for_status = lambda: None
    response.json = lambda: {"pagination": {"count": len(rows), "total": len(rows)}, "data": rows}

    with patch("requests.get", return_value=response):
        result = fetch_eod_history(
            [{"canonical_ticker": "AI.PA", "symbol": "AIR", "expected_mic": "XPAR"}],
            api_key="fake", history_days=365, max_symbols=1, min_rows=60, delay_seconds=0,
        )

    assert result.attempted == 1
    assert result.successful == 1
    assert "AI.PA" in result.frames
    assert list(result.frames["AI.PA"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(result.frames["AI.PA"]) == 80


def test_marketstack_rejects_single_wrong_exchange():
    from v182.sources.marketstack_eod import fetch_eod_history

    rows = []
    for i, date in enumerate(pd.date_range("2026-01-01", periods=70, freq="B")):
        rows.append({
            "symbol": "ABC", "exchange": "XLON",
            "date": date.strftime("%Y-%m-%dT00:00:00+0000"),
            "open": i + 1, "high": i + 2, "low": i, "close": i + 1.5, "volume": 1000,
        })
    response = MagicMock()
    response.raise_for_status = lambda: None
    response.json = lambda: {"data": rows}

    with patch("requests.get", return_value=response):
        result = fetch_eod_history(
            [{"canonical_ticker": "ABC.PA", "symbol": "ABC", "expected_mic": "XPAR"}],
            api_key="fake", max_symbols=1, min_rows=60, delay_seconds=0,
        )

    assert result.successful == 0
    assert result.failures[0]["reason"] == "NO_MATCHING_EXCHANGE"
