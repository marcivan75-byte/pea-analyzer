from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pandas as pd


def test_marketstack_resolve_one_selects_exact_mic_and_name():
    from v182.sources.marketstack_symbols import resolve_one

    response = MagicMock()
    response.raise_for_status = lambda: None
    response.json = lambda: {
        "data": [
            {"name": "Sanofi", "ticker": "SAN.PA", "stock_exchange": {"mic": "XPAR"}},
            {"name": "Sanofi India", "ticker": "SANOFI.BO", "stock_exchange": {"mic": "XBOM"}},
        ]
    }
    with patch("requests.get", return_value=response) as get:
        match, failure = resolve_one("Sanofi", "SAN.PA", "XPAR", "fake")
    assert failure is None
    assert match["symbol"] == "SAN.PA"
    assert match["confidence"] >= 0.95
    assert get.call_args.kwargs["params"]["exchange"] == "XPAR"
    assert get.call_args.kwargs["params"]["search"] == "Sanofi"


def test_marketstack_resolve_one_rejects_wrong_mic_even_with_same_name():
    from v182.sources.marketstack_symbols import resolve_one

    response = MagicMock()
    response.raise_for_status = lambda: None
    response.json = lambda: {
        "data": [{"name": "Sanofi", "ticker": "SAN", "stock_exchange": {"mic": "XNAS"}}]
    }
    with patch("requests.get", return_value=response):
        match, failure = resolve_one("Sanofi", "SAN.PA", "XPAR", "fake")
    assert match is None
    assert failure["reason"] == "NO_MATCH"


def test_marketstack_symbol_resolution_uses_cache_without_api(tmp_path):
    from v182.sources.marketstack_symbols import resolve_marketstack_symbols

    cache = tmp_path / "marketstack.csv"
    pd.DataFrame([{
        "universe": "ACTION", "isin": "FR0000120578", "name": "Sanofi",
        "original_yahoo_ticker": "SAN.PA", "expected_mic": "XPAR",
        "marketstack_symbol": "SAN.PA", "matched_name": "Sanofi", "confidence": "1.0",
        "status": "RESOLVED", "updated_at": datetime.now(timezone.utc).isoformat(),
    }]).to_csv(cache, sep=";", index=False, encoding="utf-8-sig")
    rows = pd.DataFrame([{"isin": "FR0000120578", "name": "Sanofi", "yahoo_ticker": "SAN.PA"}])

    with patch("v182.sources.marketstack_symbols.resolve_one") as resolve:
        result = resolve_marketstack_symbols(rows, "ACTION", cache, "fake", delay_seconds=0)
    resolve.assert_not_called()
    assert result.resolved["SAN.PA"] == "SAN.PA"
    assert result.cache_hits == 1
    assert result.api_attempted == 0


def test_marketstack_cache_mic_change_forces_reresolution(tmp_path):
    from v182.sources.marketstack_symbols import resolve_marketstack_symbols

    cache = tmp_path / "marketstack.csv"
    pd.DataFrame([{
        "universe": "ACTION", "isin": "FR0000120578", "name": "Sanofi",
        "original_yahoo_ticker": "SAN.PA", "expected_mic": "XLON",
        "marketstack_symbol": "SAN.L", "matched_name": "Sanofi", "confidence": "1.0",
        "status": "RESOLVED", "updated_at": datetime.now(timezone.utc).isoformat(),
    }]).to_csv(cache, sep=";", index=False, encoding="utf-8-sig")
    rows = pd.DataFrame([{"isin": "FR0000120578", "name": "Sanofi", "yahoo_ticker": "SAN.PA"}])

    fake_match = {"symbol": "SAN.PA", "matched_name": "Sanofi", "confidence": 1.0, "expected_mic": "XPAR"}
    with patch("v182.sources.marketstack_symbols.resolve_one", return_value=(fake_match, None)) as resolve:
        result = resolve_marketstack_symbols(rows, "ACTION", cache, "fake", delay_seconds=0)
    resolve.assert_called_once()
    assert result.resolved["SAN.PA"] == "SAN.PA"
    assert result.api_attempted == 1
