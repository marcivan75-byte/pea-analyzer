from __future__ import annotations

from threading import Barrier

import pandas as pd

from v182.reporting import run as enrichment_run


def test_wave5_wave6_independent_sources_overlap(monkeypatch) -> None:
    barrier = Barrier(2, timeout=2.0)
    calls: list[tuple[str, int]] = []

    def fake_wave5(actions: pd.DataFrame, api_key: str):
        calls.append(("WAVE_05", len(actions)))
        assert api_key == "secret"
        barrier.wait()
        return ([{"field": "consensus_score", "value": 4.0}], [{"reason": "F5"}])

    def fake_wave6(etfs: pd.DataFrame, cfg: dict):
        calls.append(("WAVE_06", len(etfs)))
        assert cfg == {"runtime_optimization": {}}
        barrier.wait()
        return ([{"field": "ter_pct", "value": 0.2}], [{"reason": "F6"}])

    monkeypatch.setattr(enrichment_run.waves, "wave5_consensus_finnhub", fake_wave5)
    monkeypatch.setattr(enrichment_run.waves, "wave6_etf_info", fake_wave6)

    actions = pd.DataFrame({"isin": ["A1", "A2"], "yahoo_ticker": ["A.PA", "B.PA"]})
    etfs = pd.DataFrame({"isin": ["E1"], "yahoo_ticker": ["ETF.PA"]})
    result5, result6 = enrichment_run._collect_wave5_wave6_parallel(
        actions,
        etfs,
        {"runtime_optimization": {}},
        "secret",
        run_wave5=True,
        run_wave6=True,
    )

    assert sorted(calls) == [("WAVE_05", 2), ("WAVE_06", 1)]
    assert result5 == ([{"field": "consensus_score", "value": 4.0}], [{"reason": "F5"}])
    assert result6 == ([{"field": "ter_pct", "value": 0.2}], [{"reason": "F6"}])


def test_wave6_still_runs_when_finnhub_key_is_missing(monkeypatch) -> None:
    wave5_called = False

    def fake_wave5(actions: pd.DataFrame, api_key: str):
        nonlocal wave5_called
        wave5_called = True
        return [], []

    def fake_wave6(etfs: pd.DataFrame, cfg: dict):
        return ([{"field": "ter_pct", "value": 0.2}], [])

    monkeypatch.setattr(enrichment_run.waves, "wave5_consensus_finnhub", fake_wave5)
    monkeypatch.setattr(enrichment_run.waves, "wave6_etf_info", fake_wave6)

    result5, result6 = enrichment_run._collect_wave5_wave6_parallel(
        pd.DataFrame({"isin": ["A1"], "yahoo_ticker": ["A.PA"]}),
        pd.DataFrame({"isin": ["E1"], "yahoo_ticker": ["ETF.PA"]}),
        {},
        None,
        run_wave5=True,
        run_wave6=True,
    )

    assert wave5_called is False
    assert result5 is None
    assert result6 == ([{"field": "ter_pct", "value": 0.2}], [])


def test_no_collection_when_both_waves_are_already_checkpointed(monkeypatch) -> None:
    monkeypatch.setattr(
        enrichment_run.waves,
        "wave5_consensus_finnhub",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("WAVE_05 must not run")),
    )
    monkeypatch.setattr(
        enrichment_run.waves,
        "wave6_etf_info",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("WAVE_06 must not run")),
    )

    result5, result6 = enrichment_run._collect_wave5_wave6_parallel(
        pd.DataFrame(),
        pd.DataFrame(),
        {},
        "secret",
        run_wave5=False,
        run_wave6=False,
    )

    assert result5 is None
    assert result6 is None
