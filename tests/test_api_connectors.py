from unittest.mock import MagicMock, patch
import pandas as pd
import pytest


def _response(body, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    response.json = lambda: body
    return response


def test_alpha_vantage_rejects_http_200_quota_message():
    from v182.sources.alpha_vantage import _request

    body = {"Information": "standard API rate limit reached"}
    with patch("requests.get", return_value=_response(body)):
        with pytest.raises(RuntimeError, match="ALPHA_VANTAGE_API_ERROR"):
            _request("fake", "MARKET_STATUS")


def test_alpha_vantage_search_requires_country_match():
    from v182.sources.alpha_vantage import _pick_search_match

    matches = [
        {"1. symbol": "SNY", "2. name": "Sanofi", "3. type": "Equity", "4. region": "United States", "9. matchScore": "0.99"},
        {"1. symbol": "SAN.PAR", "2. name": "Sanofi", "3. type": "Equity", "4. region": "France", "9. matchScore": "0.94"},
    ]
    best, reason = _pick_search_match(matches, "Sanofi", "FR", "FR0000120578", 0.70)
    assert reason == ""
    assert best["1. symbol"] == "SAN.PAR"


def test_alpha_vantage_daily_history_parses_global_ohlcv():
    from v182.sources.alpha_vantage import fetch_daily_history

    series = {}
    for i in range(65):
        day = pd.Timestamp("2026-05-01") + pd.Timedelta(days=i)
        series[day.strftime("%Y-%m-%d")] = {
            "1. open": "100", "2. high": "102", "3. low": "99",
            "4. close": str(100 + i / 10), "5. volume": "123456",
        }
    body = {"Meta Data": {"2. Symbol": "TSCO.LON"}, "Time Series (Daily)": series}
    with patch("requests.get", return_value=_response(body)):
        result = fetch_daily_history("TSCO.LON", "fake", "TSCO.L", min_rows=60)
    assert result.failures == []
    assert result.api_calls == 1
    assert "TSCO.L" in result.frames
    assert len(result.frames["TSCO.L"]) == 65
    assert result.frames["TSCO.L"]["Close"].notna().all()


def test_alpha_vantage_daily_history_rejects_identity_mismatch():
    from v182.sources.alpha_vantage import fetch_daily_history

    body = {
        "Meta Data": {"2. Symbol": "WRONG.LON"},
        "Time Series (Daily)": {"2026-08-01": {"1. open": "1", "2. high": "1", "3. low": "1", "4. close": "1", "5. volume": "1"}},
    }
    with patch("requests.get", return_value=_response(body)):
        result = fetch_daily_history("TSCO.LON", "fake", "TSCO.L", min_rows=1)
    assert not result.frames
    assert result.failures[0]["reason"] == "IDENTITY_MISMATCH"


def test_fred_latest_skips_missing_dot_value():
    from v182.sources.fred_macro import fetch_latest_observation

    body = {"observations": [
        {"date": "2026-08-07", "value": "."},
        {"date": "2026-08-06", "value": "17.25"},
    ]}
    with patch("requests.get", return_value=_response(body)):
        value = fetch_latest_observation("VIXCLS", "fake")
    assert value.date == "2026-08-06"
    assert value.value == 17.25


def test_fred_api_error_is_not_treated_as_empty_data():
    from v182.sources.fred_macro import fetch_latest_observation

    with patch("requests.get", return_value=_response({"error_code": 400, "error_message": "Bad Request"})):
        with pytest.raises(RuntimeError, match="FRED_API_ERROR"):
            fetch_latest_observation("VIXCLS", "fake")


def test_eia_v2_parses_latest_numeric_value():
    from v182.sources.eia_energy import fetch_latest_series

    body = {"response": {"data": [
        {"date": "2026-08-05", "value": "64.1"},
        {"date": "2026-08-07", "value": "65.3"},
        {"date": "2026-08-06", "value": "NA"},
    ]}}
    with patch("requests.get", return_value=_response(body)):
        value = fetch_latest_series("PET.RWTC.D", "fake")
    assert value.date == "2026-08-07"
    assert value.value == 65.3


def test_eia_error_payload_fails_explicitly():
    from v182.sources.eia_energy import fetch_latest_series

    with patch("requests.get", return_value=_response({"error": "invalid key", "code": 403})):
        with pytest.raises(RuntimeError, match="EIA_API_ERROR"):
            fetch_latest_series("PET.RWTC.D", "fake")


def test_fred_macro_wave_applies_three_fields_per_action(monkeypatch):
    from v182.reporting import waves
    import v182.sources.fred_macro as fred

    monkeypatch.setattr(fred, "fetch_macro_context", lambda key: {
        "source": "FRED", "api_calls": 2, "macro_vix": 18.0, "macro_curve_10y2y": 0.42,
        "macro_as_of": "2026-08-06",
        "series": {
            "macro_vix": {"series_id": "VIXCLS", "date": "2026-08-06", "value": 18.0},
            "macro_curve_10y2y": {"series_id": "T10Y2Y", "date": "2026-08-06", "value": 0.42},
        },
    })
    frame = pd.DataFrame([
        {"isin": "FR1", "name": "A"},
        {"isin": "FR2", "name": "B"},
    ])
    obs, context = waves.wave_macro_fred(frame, "fake")
    assert context["api_calls"] == 2
    assert len(obs) == 6
    assert {o["field"] for o in obs} == {"macro_vix", "macro_curve_10y2y", "macro_as_of"}


def test_alpha_process_budget_is_shared_across_universes():
    from v182.sources import history_orchestrator as orchestrator

    orchestrator._ALPHA_RUN_BUDGET.update({"initialized": False, "securities_remaining": 0})
    cfg = {"alpha_vantage": {"max_securities_per_run": 1}}
    assert orchestrator._alpha_budget(cfg) == 1
    orchestrator._consume_alpha_budget(1)
    assert orchestrator._alpha_budget(cfg) == 0
