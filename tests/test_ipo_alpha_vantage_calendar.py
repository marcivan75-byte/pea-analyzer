from __future__ import annotations

from datetime import date

import v182.decision.ipo_radar_operational_v1_1 as operational


class _Response:
    def __init__(self, text: str, payload: dict | None = None) -> None:
        self.text = text
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_alpha_vantage_calendar_parses_expected_csv_schema(monkeypatch) -> None:
    csv_text = """symbol,name,ipoDate,priceRangeLow,priceRangeHigh,currency,exchange
ALPH,Alpha Robotics,2026-09-15,14,16,USD,NASDAQ
LATE,Too Late Inc,2027-02-01,20,22,USD,NYSE
"""
    monkeypatch.setattr(operational.requests, "get", lambda *args, **kwargs: _Response(csv_text))
    rows, status = operational.collect_alpha_vantage(
        date(2026, 8, 16), date(2026, 12, 31), "key"
    )
    assert status == {"source": "ALPHA_VANTAGE", "status": "SUCCESS", "count": 1}
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Alpha Robotics"
    assert row["symbol"] == "ALPH"
    assert row["exchange"] == "NASDAQ"
    assert row["expected_date"] == "2026-09-15"
    assert row["price_low"] == 14.0
    assert row["price_high"] == 16.0
    assert row["sources"] == "ALPHA_VANTAGE"
    assert row["alpha_vantage_currency"] == "USD"


def test_alpha_vantage_missing_key_is_explicit() -> None:
    rows, status = operational.collect_alpha_vantage(
        date(2026, 8, 16), date(2026, 12, 31), None
    )
    assert rows == []
    assert status["status"] == "SKIPPED_MISSING_KEY"


def test_alpha_vantage_api_message_is_not_misparsed_as_csv(monkeypatch) -> None:
    payload = {"Information": "rate limit"}
    monkeypatch.setattr(operational.requests, "get", lambda *args, **kwargs: _Response('{"Information":"rate limit"}', payload))
    rows, status = operational.collect_alpha_vantage(
        date(2026, 8, 16), date(2026, 12, 31), "key"
    )
    assert rows == []
    assert status["status"] == "FAILED_API_MESSAGE"
    assert "rate limit" in status["detail"]
