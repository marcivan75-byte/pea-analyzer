from __future__ import annotations

import pandas as pd

from v182.mapping.action_yahoo_ticker import qualify_action_yahoo_tickers
from v182.reporting import waves
from v182.sources.yfinance_bulk import DownloadResult


def _actions(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_unqualified_symbols_are_qualified_from_canonical_mic():
    frame = _actions(
        [
            {"isin": "IT0000000001", "yahoo_ticker": "ABP", "v182_ticker_market_symbol": "ABP", "v182_ticker_canonical_mic": "XMIL"},
            {"isin": "NO0000000001", "yahoo_ticker": "AASB", "v182_ticker_market_symbol": "AASB", "v182_ticker_canonical_mic": "XOSL"},
            {"isin": "IE0000000001", "yahoo_ticker": "8GW", "v182_ticker_market_symbol": "8GW", "v182_ticker_canonical_mic": "XDUB"},
        ]
    )

    changes = qualify_action_yahoo_tickers(frame)

    assert frame["yahoo_ticker"].tolist() == ["ABP.MI", "AASB.OL", "8GW.IR"]
    assert [change.canonical_mic for change in changes] == ["XMIL", "XOSL", "XDUB"]


def test_existing_qualified_ticker_is_never_rewritten():
    frame = _actions(
        [
            {"isin": "FR0000000001", "yahoo_ticker": "AC.PA", "v182_ticker_market_symbol": "AC", "v182_ticker_canonical_mic": "XPAR"},
        ]
    )

    changes = qualify_action_yahoo_tickers(frame)

    assert changes == []
    assert frame.loc[0, "yahoo_ticker"] == "AC.PA"


def test_unsupported_venue_is_left_unchanged_instead_of_guessed():
    frame = _actions(
        [
            {"isin": "LU0000000001", "yahoo_ticker": "ABC", "v182_ticker_market_symbol": "ABC", "v182_ticker_canonical_mic": "XLUX"},
        ]
    )

    changes = qualify_action_yahoo_tickers(frame)

    assert changes == []
    assert frame.loc[0, "yahoo_ticker"] == "ABC"


def test_market_symbol_is_authoritative_base_when_qualifying():
    frame = _actions(
        [
            {"isin": "BE0000000001", "yahoo_ticker": "STALE", "v182_ticker_market_symbol": "REAL", "v182_ticker_canonical_mic": "XBRU"},
        ]
    )

    qualify_action_yahoo_tickers(frame)

    assert frame.loc[0, "yahoo_ticker"] == "REAL.BR"


def test_action_wave_downloads_only_the_qualified_runtime_symbol(monkeypatch, tmp_path):
    frame = _actions(
        [
            {"isin": "IT0000000001", "yahoo_ticker": "ABP", "v182_ticker_market_symbol": "ABP", "v182_ticker_canonical_mic": "XMIL"},
        ]
    )
    captured: dict = {}

    def fake_download_history(**kwargs):
        captured.update(kwargs)
        return DownloadResult(requested=1, successful=["ABP.MI"], failed=[], cache_file=None)

    monkeypatch.setattr(waves, "download_history", fake_download_history)
    cfg = {
        "yfinance": {
            "history_period": "5y",
            "interval": "1d",
            "actions_batch_size": 100,
            "etf_batch_size": 100,
            "auto_adjust": True,
        }
    }

    result = waves.wave_history(frame, "ACTION", str(tmp_path), cfg)

    assert captured["tickers"] == ["ABP.MI"]
    assert frame.loc[0, "yahoo_ticker"] == "ABP.MI"
    assert result.failed == []


def test_etf_wave_is_not_modified_by_action_qualification(monkeypatch, tmp_path):
    frame = _actions(
        [
            {"isin": "ETF1", "yahoo_ticker": "RAW", "v182_ticker_market_symbol": "RAW", "v182_ticker_canonical_mic": "XMIL"},
        ]
    )
    captured: dict = {}

    def fake_download_history(**kwargs):
        captured.update(kwargs)
        return DownloadResult(requested=1, successful=["RAW"], failed=[], cache_file=None)

    monkeypatch.setattr(waves, "download_history", fake_download_history)
    cfg = {
        "yfinance": {
            "history_period": "5y",
            "interval": "1d",
            "actions_batch_size": 100,
            "etf_batch_size": 100,
            "auto_adjust": True,
        }
    }

    waves.wave_history(frame, "ETF", str(tmp_path), cfg)

    assert captured["tickers"] == ["RAW"]
    assert frame.loc[0, "yahoo_ticker"] == "RAW"
