from __future__ import annotations

import threading

import pandas as pd

from v182.features import topdown_features
from v182.sources import fred_macro
from v182.sources.fred_macro import MacroScore
from v182.sources.gdelt_news import NewsScore


def test_fred_global_series_start_concurrently_but_results_remain_governed_order(monkeypatch) -> None:
    barrier=threading.Barrier(len(fred_macro.GLOBAL_SERIES))
    started=[]
    lock=threading.Lock()

    def fake_fetch(series_id: str, api_key: str):
        assert api_key == "KEY"
        with lock:
            started.append(series_id)
        barrier.wait(timeout=3)
        return [float(value) for value in range(1,31)],None

    monkeypatch.setattr(fred_macro,"fetch_series",fake_fetch)
    result=fred_macro.global_macro_score("KEY")

    assert set(started) == set(fred_macro.GLOBAL_SERIES)
    assert list(result.components) == list(fred_macro.GLOBAL_SERIES)
    assert result.coverage == 1.0
    assert result.score == 25.0
    assert result.errors == {}


def test_topdown_overlaps_fred_and_gdelt_without_changing_gdelt_policy(monkeypatch) -> None:
    barrier=threading.Barrier(2)
    captured={}

    def fake_macro(api_key: str | None) -> MacroScore:
        assert api_key == "FRED_KEY"
        barrier.wait(timeout=3)
        return MacroScore(
            score=61.25,
            coverage=1.0,
            components={"VIXCLS":60.0,"BAMLH0A0HYM2":62.0,"T10Y2Y":63.0},
            errors={},
            source="FRED",
        )

    def fake_score_queries(queries, *, timespan: str, max_records: int, delay_seconds: float, max_workers: int):
        captured["queries"]=list(queries)
        captured["timespan"]=timespan
        captured["max_records"]=max_records
        captured["delay_seconds"]=delay_seconds
        captured["max_workers"]=max_workers
        barrier.wait(timeout=3)
        return {
            query:(NewsScore(None,0,0,0,"GDELT"),None)
            for query in queries
        }

    monkeypatch.setattr(topdown_features,"global_macro_score",fake_macro)
    monkeypatch.setattr(topdown_features,"score_queries",fake_score_queries)

    actions=pd.DataFrame([
        {
            "isin":"FR0000000001",
            "name":"Action Test",
            "country_yf":"France",
            "sector_yf":"Technology",
            "market_cap":"1000000",
            "perf_1m_pct":"1.0",
            "perf_6m_pct":"4.0",
        }
    ])
    etfs=pd.DataFrame([
        {
            "isin":"FR0010000001",
            "name":"ETF Test",
            "country_yf":"France",
            "sector_yf":"Technology",
            "perf_1m_pct":"2.0",
            "perf_6m_pct":"5.0",
        }
    ])

    result=topdown_features.build_topdown(
        actions,
        etfs,
        fred_api_key="FRED_KEY",
        instrument_news_top_n=1,
    )

    assert result.global_scores["funnel_global_macro_score"] == 61.25
    assert result.provenance["funnel_global_macro_score"] == "FRED"
    assert captured["timespan"] == "2d"
    assert captured["max_records"] == 50
    assert captured["delay_seconds"] == 0.12
    assert captured["max_workers"] == 6
    assert "(markets OR economy OR stocks OR bonds)" in captured["queries"]
    assert '"Action Test"' in captured["queries"]
    assert any('"France"' in query for query in captured["queries"])
    assert any('"Technology"' in query for query in captured["queries"])


def test_fred_missing_key_behavior_is_unchanged() -> None:
    result=fred_macro.global_macro_score(None)
    assert result.score is None
    assert result.coverage == 0.0
    assert result.errors == {"FRED_API_KEY":"MISSING"}
    assert result.source == "FRED"
