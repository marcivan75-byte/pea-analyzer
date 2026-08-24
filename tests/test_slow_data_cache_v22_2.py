from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting import slow_data_cache_v22_2 as cache


def test_etf_frame_cache_reuses_success_and_refreshes_only_new_isin(tmp_path: Path):
    calls = []

    def collector(frame, **kwargs):
        calls.append(frame["isin"].astype(str).tolist())
        obs = [
            {"isin": isin, "field": "ter_pct", "value": 0.2, "source": "test"}
            for isin in frame["isin"].astype(str)
        ]
        return obs, [], {"requested": len(frame)}

    metrics = {}
    wrapped = cache.cached_etf_frame_collector(
        collector, cache.ETF_STRUCTURAL, root=tmp_path, metrics=metrics
    )
    first = pd.DataFrame({"isin": ["ETF1", "ETF2"], "provider": ["A", "A"]})
    obs1, failures1, _ = wrapped(first)
    assert len(obs1) == 2 and not failures1
    assert calls == [["ETF1", "ETF2"]]

    second = pd.DataFrame({"isin": ["ETF1", "ETF2", "ETF3"], "provider": ["A", "A", "A"]})
    obs2, failures2, metrics2 = wrapped(second)
    assert len(obs2) == 3 and not failures2
    assert calls[-1] == ["ETF3"]
    assert metrics2["cache_hits"] == 2
    assert metrics2["network_requested_after_cache"] == 1


def test_failed_isin_is_not_cached(tmp_path: Path):
    calls = []

    def collector(frame, **kwargs):
        keys = frame["isin"].astype(str).tolist()
        calls.append(keys)
        obs = []
        failures = []
        for isin in keys:
            if isin == "BAD":
                failures.append({"isin": isin, "reason": "TEST_FAILURE"})
            else:
                obs.append({"isin": isin, "field": "official_benchmark", "value": "IDX"})
        return obs, failures, {"requested": len(frame)}

    wrapped = cache.cached_etf_frame_collector(
        collector, cache.ETF_STRUCTURAL, root=tmp_path, metrics={}
    )
    frame = pd.DataFrame({"isin": ["GOOD", "BAD"], "provider": ["A", "A"]})
    wrapped(frame)
    wrapped(frame)
    assert calls[0] == ["GOOD", "BAD"]
    assert calls[1] == ["BAD"]


def test_ticker_cache_reuses_success_and_retries_failure(tmp_path: Path):
    calls = []

    def collector(tickers, **kwargs):
        calls.append(list(tickers))
        obs = []
        failures = []
        for ticker in tickers:
            if ticker == "FAIL":
                failures.append({"ticker": ticker, "reason": "NO_DATA"})
            else:
                obs.append({"ticker": ticker, "field": "direct_sector_hhi", "value": 0.1})
        return obs, failures

    wrapped = cache.cached_ticker_collector(
        collector, cache.ETF_FUND_STRUCTURE, root=tmp_path, metrics={}
    )
    wrapped(["OK", "FAIL"])
    wrapped(["OK", "FAIL"])
    assert calls == [["FAIL", "OK"], ["FAIL"]]


def test_cache_policies_keep_monthly_and_static_cadences_separate():
    assert cache.ETF_STRUCTURAL.ttl_days == 30
    assert cache.ETF_FUND_STRUCTURE.ttl_days == 30
    assert cache.ETF_INCEPTION.ttl_days == 365
    assert cache.ETF_STRUCTURAL.cadence == "MONTHLY"
    assert cache.ETF_INCEPTION.cadence == "QUASI_STATIC"
