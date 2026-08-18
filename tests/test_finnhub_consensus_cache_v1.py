from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pandas as pd

from v182.reporting import waves
from v182.sources import finnhub_consensus as consensus


def _obs_for(ticker: str, value: float = 4.0) -> list[dict]:
    return [
        {"ticker": ticker, "field": "consensus_score", "value": value, "source": "Finnhub"},
        {"ticker": ticker, "field": "consensus_rating", "value": "BUY", "source": "Finnhub"},
    ]


def test_cache_bootstrap_fetches_entire_uncached_universe_even_when_budget_is_small(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_fetch(tickers, api_key, delay_seconds=1.1, max_workers=8):
        calls.append(list(tickers))
        rows = [row for ticker in tickers for row in _obs_for(ticker)]
        return rows, []

    monkeypatch.setattr(consensus, "fetch_consensus", fake_fetch)
    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    tickers = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    observations, failures, metrics = consensus.fetch_consensus_cached(
        tickers, "key", tmp_path / "cache.json", refresh_budget=2, now=now,
    )

    assert failures == []
    assert calls == [sorted(tickers)]
    assert metrics["mandatory_refresh_count"] == 5
    assert metrics["live_refresh_requested"] == 5
    assert metrics["full_universe_preserved"] is True
    assert {row["ticker"] for row in observations} == set(tickers)
    assert {row["cache_state"] for row in observations} == {"LIVE_REFRESH"}


def test_warm_cache_rotates_only_budget_and_preserves_original_cache_timestamp(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_fetch(tickers, api_key, delay_seconds=1.1, max_workers=8):
        calls.append(list(tickers))
        rows = [row for ticker in tickers for row in _obs_for(ticker, 4.2)]
        return rows, []

    monkeypatch.setattr(consensus, "fetch_consensus", fake_fetch)
    cache_path = tmp_path / "cache.json"
    day0 = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    tickers = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    consensus.fetch_consensus_cached(tickers, "key", cache_path, refresh_budget=1, now=day0)
    calls.clear()

    observations, failures, metrics = consensus.fetch_consensus_cached(
        tickers, "key", cache_path, refresh_budget=2, now=day0 + timedelta(days=1),
    )
    assert failures == []
    assert calls == [["AAA", "BBB"]]
    assert metrics["live_refresh_requested"] == 2
    assert metrics["cache_hit_tickers"] == 3

    by_ticker: dict[str, list[dict]] = {}
    for row in observations:
        by_ticker.setdefault(row["ticker"], []).append(row)
    assert {row["cache_state"] for row in by_ticker["AAA"]} == {"LIVE_REFRESH"}
    assert {row["cache_state"] for row in by_ticker["CCC"]} == {"CACHE_HIT"}
    assert {row["fetched_at_utc"] for row in by_ticker["CCC"]} == {day0.isoformat()}
    assert {row["fetched_at_utc"] for row in by_ticker["AAA"]} == {(day0 + timedelta(days=1)).isoformat()}


def test_expired_cache_is_not_reused_after_transient_refresh_failure(tmp_path, monkeypatch) -> None:
    cache_path = tmp_path / "cache.json"
    old = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    cache_path.write_text(
        json.dumps({
            "version": consensus.CACHE_VERSION,
            "entries": {
                "AAA": {
                    "status": "OK",
                    "fetched_at_utc": old.isoformat(),
                    "observations": [{"field": "consensus_score", "value": 4.0}],
                }
            },
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        consensus,
        "fetch_consensus",
        lambda tickers, api_key, delay_seconds=1.1, max_workers=8: (
            [], [{"ticker": "AAA", "reason": "Timeout", "detail": "network"}]
        ),
    )
    now = old + timedelta(days=11)
    observations, failures, metrics = consensus.fetch_consensus_cached(
        ["AAA"], "key", cache_path, refresh_budget=1, max_cache_age_days=10, now=now,
    )
    assert observations == []
    assert metrics["mandatory_refresh_count"] == 1
    assert metrics["expired_after_refresh_failure"] == 1
    assert metrics["unusable_tickers"] == 1
    assert any(row["reason"] == "Timeout" for row in failures)


def test_recent_cache_can_be_used_after_transient_refresh_failure(tmp_path, monkeypatch) -> None:
    cache_path = tmp_path / "cache.json"
    old = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    cache_path.write_text(
        json.dumps({
            "version": consensus.CACHE_VERSION,
            "entries": {
                "AAA": {
                    "status": "OK",
                    "fetched_at_utc": old.isoformat(),
                    "observations": [{"field": "consensus_score", "value": 4.0}],
                }
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        consensus,
        "fetch_consensus",
        lambda tickers, api_key, delay_seconds=1.1, max_workers=8: (
            [], [{"ticker": "AAA", "reason": "Timeout", "detail": "network"}]
        ),
    )
    observations, failures, metrics = consensus.fetch_consensus_cached(
        ["AAA"], "key", cache_path, refresh_budget=1, max_cache_age_days=10,
        now=old + timedelta(days=2),
    )
    assert len(observations) == 1
    assert observations[0]["cache_state"] == "CACHE_HIT"
    assert observations[0]["fetched_at_utc"] == old.isoformat()
    assert metrics["transient_cache_fallbacks"] == 1
    assert any(row["reason"] == "LIVE_REFRESH_FAILED_CACHE_FALLBACK_USED" for row in failures)


def test_negative_cache_avoids_immediate_repeat_calls(tmp_path, monkeypatch) -> None:
    calls = 0

    def fake_fetch(tickers, api_key, delay_seconds=1.1, max_workers=8):
        nonlocal calls
        calls += 1
        return [], [{"ticker": ticker, "reason": "NO_RECOMMENDATION_DATA"} for ticker in tickers]

    monkeypatch.setattr(consensus, "fetch_consensus", fake_fetch)
    cache_path = tmp_path / "cache.json"
    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    consensus.fetch_consensus_cached(["AAA"], "key", cache_path, refresh_budget=400, now=now)
    _, _, metrics = consensus.fetch_consensus_cached(
        ["AAA"], "key", cache_path, refresh_budget=400, negative_cache_days=3,
        now=now + timedelta(days=1),
    )
    assert calls == 1
    assert metrics["negative_cache_hits"] == 1
    assert metrics["live_refresh_requested"] == 0


def test_wave5_preserves_cached_timestamp_and_marks_cache_source(monkeypatch) -> None:
    cached_at = "2026-08-12T10:30:00+00:00"

    def fake_cached(tickers, api_key, cache_path, **kwargs):
        return ([{
            "ticker": "AAA.PA",
            "field": "consensus_score",
            "value": 4.25,
            "source": "Finnhub",
            "fetched_at_utc": cached_at,
            "cache_state": "CACHE_HIT",
        }], [], {"requested": 1, "cache_hit_tickers": 1, "full_universe_preserved": True})

    monkeypatch.setattr(consensus, "fetch_consensus_cached", fake_cached)
    actions = pd.DataFrame({"isin": ["FR0000000001"], "yahoo_ticker": ["AAA.PA"]})
    observations, failures = waves.wave5_consensus_finnhub(actions, "key")
    assert failures == []
    assert len(observations) == 1
    row = observations[0]
    assert row["source"] == "Finnhub_CACHE"
    assert row["collected_at"] == cached_at
    assert row["as_of"] == "2026-08-12"
    assert row["evidence_level"] == "B"
