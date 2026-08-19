from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from v182.sources import yfinance_info


def _live_rows(ticker: str, earnings_ts: int) -> list[dict]:
    return [
        {"ticker": ticker, "field": "market_cap", "value": 1_000_000, "source": "yfinance"},
        {"ticker": ticker, "field": "current_price_yf", "value": 123.4, "source": "yfinance"},
        {"ticker": ticker, "field": "next_earnings_timestamp_yf", "value": earnings_ts, "source": "yfinance"},
        {"ticker": ticker, "field": "days_to_earnings", "value": 5.0, "source": "yfinance"},
        {"ticker": ticker, "field": "earnings_within_7d_flag", "value": 1.0, "source": "yfinance"},
        {"ticker": ticker, "field": "earnings_within_30d_flag", "value": 1.0, "source": "yfinance"},
    ]


def test_cache_hit_avoids_repeat_request_and_drops_stale_current_price(tmp_path, monkeypatch):
    calls = []
    t0 = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
    earnings_ts = int((t0 + timedelta(days=5)).timestamp())

    def fake_collect(tickers, delay_seconds=0.4, max_workers=4):
        calls.append(tuple(tickers))
        rows = []
        for ticker in tickers:
            rows.extend(_live_rows(ticker, earnings_ts))
        return rows, []

    monkeypatch.setattr(yfinance_info, "collect_info", fake_collect)
    cache = tmp_path / "info.json"

    first, failures1, metrics1 = yfinance_info.collect_info_cached(
        ["AIR.PA"], cache, max_cache_age_days=7, now=t0
    )
    second, failures2, metrics2 = yfinance_info.collect_info_cached(
        ["AIR.PA"], cache, max_cache_age_days=7, now=t0 + timedelta(days=1)
    )

    assert failures1 == []
    assert failures2 == []
    assert calls == [("AIR.PA",)]
    assert metrics1["live_refresh_requested"] == 1
    assert metrics2["live_refresh_requested"] == 0
    assert metrics2["cache_hit_tickers"] == 1
    assert any(row["field"] == "current_price_yf" for row in first)
    assert not any(row["field"] == "current_price_yf" for row in second)
    days = [row["value"] for row in second if row["field"] == "days_to_earnings"]
    assert days == [pytest.approx(4.0)]
    assert all(row.get("source") == "yfinance_CACHE" for row in second)


def test_expired_entry_is_refreshed_and_never_replayed_after_failed_refresh(tmp_path, monkeypatch):
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    earnings_ts = int((t0 + timedelta(days=20)).timestamp())
    cache = tmp_path / "info.json"

    monkeypatch.setattr(
        yfinance_info,
        "collect_info",
        lambda tickers, **kwargs: (_live_rows("MC.PA", earnings_ts), []),
    )
    yfinance_info.collect_info_cached(["MC.PA"], cache, max_cache_age_days=7, now=t0)

    monkeypatch.setattr(
        yfinance_info,
        "collect_info",
        lambda tickers, **kwargs: ([], [{"ticker": "MC.PA", "error": "Timeout"}]),
    )
    rows, failures, metrics = yfinance_info.collect_info_cached(
        ["MC.PA"], cache, max_cache_age_days=7, now=t0 + timedelta(days=8)
    )

    assert rows == []
    assert failures and failures[0]["ticker"] == "MC.PA"
    assert metrics["expired_after_failure"] == 1
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert "MC.PA" not in payload["entries"]


def test_cache_file_is_atomic_and_versioned(tmp_path, monkeypatch):
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    monkeypatch.setattr(
        yfinance_info,
        "collect_info",
        lambda tickers, **kwargs: ([{"ticker": "SAN.PA", "field": "market_cap", "value": 42, "source": "yfinance"}], []),
    )
    cache = tmp_path / "nested" / "yf.json"
    yfinance_info.collect_info_cached(["SAN.PA"], cache, now=now)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["version"] == yfinance_info.CACHE_VERSION
    assert payload["entries"]["SAN.PA"]["status"] == "OK"
    assert not Path(str(cache) + ".tmp").exists()
