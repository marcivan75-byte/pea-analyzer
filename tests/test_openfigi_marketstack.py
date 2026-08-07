from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np


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


def test_openfigi_master_map_builds_yahoo_and_marketstack_identifiers(tmp_path):
    from v182.mapping.etf_isin_resolver import build_openfigi_master_map

    actions = pd.DataFrame([{"isin": "FR0000120073", "yahoo_ticker": "AI.PA"}])
    etfs = pd.DataFrame(columns=["isin", "yahoo_ticker"])
    fake = {
        "FR0000120073": [{
            "figi": "BBGTEST",
            "compositeFIGI": "BBGCOMP",
            "shareClassFIGI": "BBGSHARE",
            "ticker": "AIR",
            "exchCode": "PA",
        }]
    }
    output = tmp_path / "openfigi.csv"
    with patch("v182.mapping.etf_isin_resolver.resolve_isins", return_value=fake):
        summary = build_openfigi_master_map(actions, etfs, output, api_key="fake")

    mapped = pd.read_csv(output, sep=";", dtype=str).fillna("")
    assert summary["resolved"] == 1
    assert mapped.iloc[0]["yahoo_candidate"] == "AIR.PA"
    assert mapped.iloc[0]["openfigi_mic"] == "XPAR"
    assert mapped.iloc[0]["figi"] == "BBGTEST"


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


def test_marketstack_rejects_ambiguous_wrong_exchange():
    from v182.sources.marketstack_eod import fetch_eod_history

    rows = []
    for mic in ("XLON", "XNAS"):
        for i, date in enumerate(pd.date_range("2026-01-01", periods=70, freq="B")):
            rows.append({
                "symbol": "ABC", "exchange": mic,
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
