from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd

from v182.sources.investing_technical import (
    _safe_investing_url,
    collect_technical_context_cached,
)


class FakeResponse:
    def __init__(self, text: str, url: str):
        self.text = text
        self.url = url

    def raise_for_status(self):
        return None


def _row() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "isin": "FR0000000001",
            "asset_class": "ACTION",
            "horizon": "CT",
            "name": "Alpha Test SA",
            "yahoo_ticker": "ALPHA.PA",
        }
    ])


def test_safe_url_rejects_non_instrument_paths_wrong_hosts_and_technical_as_base():
    base = "https://www.investing.com/equities/air-liquide"
    technical = base + "-technical"
    assert _safe_investing_url(base) is True
    assert _safe_investing_url(technical) is True
    assert _safe_investing_url(base, allow_technical=False) is True
    assert _safe_investing_url(technical, allow_technical=False) is False
    assert _safe_investing_url("https://www.investing.com/etfs/amundi-test") is True
    assert _safe_investing_url("https://www.investing.com/news/stock-market-news") is False
    assert _safe_investing_url("https://example.com/equities/air-liquide") is False
    assert _safe_investing_url("http://www.investing.com/equities/air-liquide") is False


def test_unresolved_mapping_is_persisted_without_raw_html_and_skips_network_during_cooldown(tmp_path: Path):
    calls: list[str] = []

    def unresolved_fetcher(url, timeout):
        calls.append(url)
        return FakeResponse("page without requested isin", url)

    cache = tmp_path / "technical.json"
    mapping = tmp_path / "mapping.json"
    now = datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)
    first = collect_technical_context_cached(
        _row(), cache, mapping, refresh_budget=1, request_start_interval_seconds=0,
        unmapped_retry_ttl_hours=24, fetcher=unresolved_fetcher, now=now,
    )
    assert first.metrics["live_refresh_requested"] == 1
    assert first.metrics["live_refresh_success"] == 0
    assert len(calls) >= 1
    payload = json.loads(mapping.read_text(encoding="utf-8"))
    entry = payload["entries"]["FR0000000001"]
    assert entry["status"] == "UNRESOLVED"
    assert entry["reason"] == "NO_VALIDATED_PUBLIC_URL"
    assert entry["failure_count"] == 1
    assert "page without requested isin" not in mapping.read_text(encoding="utf-8")

    def must_not_call(url, timeout):
        raise AssertionError(f"network call forbidden during negative cooldown: {url}")

    second = collect_technical_context_cached(
        _row(), cache, mapping, refresh_budget=1, request_start_interval_seconds=0,
        unmapped_retry_ttl_hours=24, fetcher=must_not_call, now=now + timedelta(hours=1),
    )
    assert second.metrics["live_refresh_requested"] == 0
    assert second.metrics["resolution_cooldown_skipped"] == 1
    assert second.metrics["permanent_blacklist"] is False


def test_expired_negative_cache_retries_and_replaces_unresolved_mapping(tmp_path: Path):
    cache = tmp_path / "technical.json"
    mapping = tmp_path / "mapping.json"
    now = datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)
    mapping.write_text(json.dumps({
        "version": "INVESTING_URL_MAP_V1",
        "entries": {"FR0000000001": {
            "status": "UNRESOLVED",
            "last_failed_at_utc": (now - timedelta(hours=25)).isoformat(),
            "reason": "NO_VALIDATED_PUBLIC_URL",
            "failure_count": 2,
        }},
    }), encoding="utf-8")
    calls: list[str] = []

    def resolved_fetcher(url, timeout):
        calls.append(url)
        if url.endswith("-technical"):
            return FakeResponse("Daily Strong Buy Weekly Strong Buy Monthly Buy", url)
        return FakeResponse("FR0000000001 Alpha Test", "https://www.investing.com/equities/alpha-test")

    result = collect_technical_context_cached(
        _row(), cache, mapping, refresh_budget=1, request_start_interval_seconds=0,
        unmapped_retry_ttl_hours=24, fetcher=resolved_fetcher, now=now,
    )
    assert result.metrics["live_refresh_requested"] == 1
    assert result.metrics["live_refresh_success"] == 1
    assert any(url.endswith("-technical") for url in calls)
    mapped = json.loads(mapping.read_text(encoding="utf-8"))["entries"]["FR0000000001"]
    assert mapped["status"] == "RESOLVED"
    assert mapped["validated_isin"] == "FR0000000001"
    technical = json.loads(cache.read_text(encoding="utf-8"))["entries"]["FR0000000001"]
    assert technical["fields"]["investing_daily_signal"] == "STRONG_BUY"
    assert technical["fields"]["investing_weekly_signal"] == "STRONG_BUY"
    assert technical["fields"]["investing_monthly_signal"] == "BUY"


def test_corrupt_technical_url_stored_as_base_is_re_resolved_not_doubled(tmp_path: Path):
    cache = tmp_path / "technical.json"
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({
        "version": "INVESTING_URL_MAP_V1",
        "entries": {"FR0000000001": {
            "status": "RESOLVED",
            "base_url": "https://www.investing.com/equities/alpha-test-technical",
            "validated_isin": "FR0000000001",
        }},
    }), encoding="utf-8")
    calls: list[str] = []

    def fetcher(url, timeout):
        calls.append(url)
        assert not url.endswith("-technical-technical")
        if url.endswith("-technical"):
            return FakeResponse("Daily Strong Buy Weekly Strong Buy Monthly Strong Buy", url)
        return FakeResponse("FR0000000001 Alpha Test", "https://www.investing.com/equities/alpha-test")

    result = collect_technical_context_cached(
        _row(), cache, mapping, refresh_budget=1, request_start_interval_seconds=0,
        fetcher=fetcher, now=datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
    )
    assert result.metrics["live_refresh_success"] == 1
    mapped = json.loads(mapping.read_text(encoding="utf-8"))["entries"]["FR0000000001"]
    assert mapped["base_url"] == "https://www.investing.com/equities/alpha-test"


def test_unsafe_redirect_never_populates_technical_cache(tmp_path: Path):
    cache = tmp_path / "technical.json"
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({
        "version": "INVESTING_URL_MAP_V1",
        "entries": {"FR0000000001": {
            "status": "RESOLVED",
            "base_url": "https://www.investing.com/equities/alpha-test",
            "validated_isin": "FR0000000001",
        }},
    }), encoding="utf-8")

    def redirect_fetcher(url, timeout):
        return FakeResponse("Daily Strong Buy Weekly Strong Buy Monthly Strong Buy", "https://www.investing.com/news/redirected")

    result = collect_technical_context_cached(
        _row(), cache, mapping, refresh_budget=1, request_start_interval_seconds=0,
        fetcher=redirect_fetcher, now=datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
    )
    assert result.metrics["live_refresh_success"] == 0
    assert any(row["reason"] == "ValueError" for row in result.failures)
    assert not cache.exists()
