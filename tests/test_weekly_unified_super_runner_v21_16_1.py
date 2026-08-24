from __future__ import annotations

from threading import Event

import pandas as pd
import pytest

from v182.reporting import weekly_unified_super_runner_v21_16_1 as weekly


def _price_frame(tickers: list[str]) -> pd.DataFrame:
    columns = pd.MultiIndex.from_tuples([(ticker, "Close") for ticker in tickers])
    return pd.DataFrame(
        [[float(index + 1) for index in range(len(tickers))]],
        index=pd.DatetimeIndex(["2026-08-21"]),
        columns=columns,
    )


def test_parallel_safe_horizons_preserves_requested_order(monkeypatch):
    frame = pd.DataFrame({"isin": ["A", "B"]})
    registry = {"version": "TEST"}

    def decisions(_frame, _registry, asset_class, horizons):
        horizon = list(horizons)[0]
        return pd.DataFrame({"asset_class": [asset_class], "horizon": [horizon]})

    def coverage(_frame, _registry, asset_class, horizons):
        horizon = list(horizons)[0]
        return pd.DataFrame({"asset_class": [asset_class], "horizon": [horizon], "criterion": ["x"]})

    monkeypatch.setattr(weekly.committee_master_run, "decisions_from_scores", decisions)
    monkeypatch.setattr(weekly.committee_master_run, "criterion_coverage_report", coverage)

    requested = ["CT", "MT", "SHORT", "TOP_DOWN"]
    decision_parts, coverage_parts, failures = weekly._parallel_safe_horizons(
        frame, registry, "ACTION", requested
    )

    assert [part.iloc[0]["horizon"] for part in decision_parts] == requested
    assert [part.iloc[0]["horizon"] for part in coverage_parts] == requested
    assert failures == []


def test_parallel_reference_scoring_preserves_horizon_order():
    def original(_frame, _registry, asset_class, horizons):
        horizon = list(horizons)[0]
        return pd.DataFrame({"asset_class": [asset_class], "horizon": [horizon], "score": [1.0]})

    wrapped = weekly._parallel_decisions_from_scores(original)
    requested = ["CT", "MT", "SHORT", "TOP_DOWN"]
    result = wrapped(pd.DataFrame({"isin": ["A"]}), {}, "ACTION", requested)
    assert result["horizon"].tolist() == requested


def test_memoized_resolver_reuses_same_frame_and_field():
    frame = pd.DataFrame({"metric": [1.0, 2.0]})
    calls: list[str] = []

    def original(input_frame, name):
        calls.append(name)
        return input_frame[name].astype(float), f"DIRECT:{name}"

    wrapped, stats = weekly._memoized_resolver(original)
    first_values, first_source = wrapped(frame, "metric")
    second_values, second_source = wrapped(frame, "metric")

    pd.testing.assert_series_equal(first_values, second_values)
    assert first_source == second_source == "DIRECT:metric"
    assert calls == ["metric"]
    assert stats == {"hits": 1, "misses": 1, "waits": 0, "entries": 1, "frames": 1}


def test_memoized_resolver_never_reuses_across_frames():
    frame_a = pd.DataFrame({"metric": [1.0]})
    frame_b = pd.DataFrame({"metric": [2.0]})
    calls: list[float] = []

    def original(input_frame, name):
        calls.append(float(input_frame[name].iloc[0]))
        return input_frame[name].astype(float), f"DIRECT:{name}"

    wrapped, stats = weekly._memoized_resolver(original)
    values_a, _ = wrapped(frame_a, "metric")
    values_b, _ = wrapped(frame_b, "metric")

    assert values_a.iloc[0] == 1.0
    assert values_b.iloc[0] == 2.0
    assert calls == [1.0, 2.0]
    assert stats["misses"] == 2
    assert stats["hits"] == 0
    assert stats["entries"] == 2
    assert stats["frames"] == 2


def test_yfinance_partial_batch_retries_missing_symbols_as_group():
    calls: list[list[str]] = []

    def original_download(
        _yf,
        tickers,
        _period,
        _interval,
        _auto_adjust,
        _actions_requested,
        *,
        threads,
        start=None,
    ):
        requested = list(tickers)
        calls.append(requested)
        if requested == ["AAA", "BBB"]:
            return _price_frame(["AAA"])
        if requested == ["BBB"]:
            return _price_frame(["BBB"])
        raise AssertionError(f"unexpected request: {requested}")

    def original_download_one(*args, **kwargs):
        raise AssertionError("singleton fallback should not be needed")

    wrapped, _, stats = weekly._weekly_yfinance_retry_hardening(
        original_download, original_download_one
    )
    result = wrapped(
        object(),
        ["AAA", "BBB"],
        "1mo",
        "1d",
        True,
        True,
        threads=True,
    )

    assert weekly.yfinance_bulk._contains_ticker(result, "AAA")
    assert weekly.yfinance_bulk._contains_ticker(result, "BBB")
    assert calls == [["AAA", "BBB"], ["BBB"]]
    assert stats["partial_batches"] == 1
    assert stats["grouped_retry_calls"] == 1
    assert stats["grouped_retry_recovered_tickers"] == 1


def test_yfinance_double_batch_failure_fast_skips_only_cached_singletons():
    batch_calls: list[list[str]] = []
    singleton_calls: list[tuple[str, str | None]] = []

    def original_download(
        _yf,
        tickers,
        _period,
        _interval,
        _auto_adjust,
        _actions_requested,
        *,
        threads,
        start=None,
    ):
        requested = list(tickers)
        batch_calls.append(requested)
        raise TimeoutError("provider unavailable")

    def original_download_one(
        _yf,
        ticker,
        _period,
        _interval,
        _auto_adjust,
        _actions_requested,
        *,
        start=None,
    ):
        singleton_calls.append((ticker, start))
        return _price_frame([ticker])

    wrapped, wrapped_one, stats = weekly._weekly_yfinance_retry_hardening(
        original_download, original_download_one
    )

    with pytest.raises(TimeoutError):
        wrapped(
            object(),
            ["AAA", "BBB"],
            "1mo",
            "1d",
            True,
            True,
            threads=True,
        )

    cached_retry = wrapped_one(
        object(), "AAA", "1mo", "1d", True, True, start=None
    )
    new_ticker_retry = wrapped_one(
        object(), "NEW", "5y", "1d", True, True, start="2023-01-01"
    )

    assert cached_retry.empty
    assert weekly.yfinance_bulk._contains_ticker(new_ticker_retry, "NEW")
    assert batch_calls == [["AAA", "BBB"], ["AAA", "BBB"]]
    assert singleton_calls == [("NEW", "2023-01-01")]
    assert stats["multi_batch_errors"] == 1
    assert stats["grouped_retry_calls"] == 1
    assert stats["grouped_retry_errors"] == 1
    assert stats["circuit_open_events"] == 1
    assert stats["cached_singleton_fast_skips"] == 1
    assert stats["singleton_calls"] == 1


def test_sector_starts_after_structure_and_before_historical_sector_join(monkeypatch, tmp_path):
    sector_started = Event()
    calls: list[str] = []

    def refresh():
        calls.append("refresh")
        return {"status": "SUCCESS"}

    def structure(root):
        calls.append("structure")
        return {"status": "SUCCESS"}

    def sector(root):
        calls.append("sector_actual")
        sector_started.set()
        return {"status": "SUCCESS"}

    monkeypatch.setattr(weekly.base.enrichment_run, "run", refresh)
    monkeypatch.setattr(weekly.base.etf_structure_refresh, "run", structure)
    monkeypatch.setattr(weekly.base.sector_rotation_v2_shadow_run, "run", sector)

    def fake_base_run(root):
        weekly.base.enrichment_run.run()
        weekly.base.etf_structure_refresh.run(root)
        assert sector_started.wait(timeout=2.0)
        calls.append("etf_mt_remaining")
        result = weekly.base.sector_rotation_v2_shadow_run.run(root)
        calls.append("committee_after_sector_join")
        assert result["status"] == "SUCCESS"
        return {"status": "SUCCESS"}

    monkeypatch.setattr(weekly.base, "run", fake_base_run)

    payload = weekly.run(root=tmp_path)

    assert payload["status"] == "SUCCESS"
    assert calls.index("structure") < calls.index("sector_actual")
    assert calls.index("sector_actual") < calls.index("committee_after_sector_join")
    assert calls.index("sector_actual") <= calls.index("etf_mt_remaining")
    audit = (tmp_path / "outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V21_16_1.json").read_text()
    assert '"committee_waits_for_sector_rotation": true' in audit
    assert '"criterion_resolution_function_wrapped": true' in audit
    assert '"yfinance_grouped_retry_before_singleton": true' in audit
    assert '"decision_logic_changed": false' in audit


def test_refresh_failure_does_not_start_background_sector(monkeypatch, tmp_path):
    calls: list[str] = []

    def refresh_failure():
        calls.append("refresh_failure")
        raise RuntimeError("REFRESH_FAILURE")

    def structure(root):
        calls.append("structure")
        return {"status": "SUCCESS"}

    def sector(root):
        calls.append("sector")
        return {"status": "SUCCESS"}

    monkeypatch.setattr(weekly.base.enrichment_run, "run", refresh_failure)
    monkeypatch.setattr(weekly.base.etf_structure_refresh, "run", structure)
    monkeypatch.setattr(weekly.base.sector_rotation_v2_shadow_run, "run", sector)

    def fake_base_run(root):
        try:
            weekly.base.enrichment_run.run()
        except RuntimeError as exc:
            assert str(exc) == "REFRESH_FAILURE"
        weekly.base.etf_structure_refresh.run(root)
        # Historical unified_runner would skip sector after failed refresh.
        return {"status": "PARTIAL_SUCCESS"}

    monkeypatch.setattr(weekly.base, "run", fake_base_run)

    payload = weekly.run(root=tmp_path)
    assert payload["status"] == "PARTIAL_SUCCESS"
    assert "sector" not in calls
