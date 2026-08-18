from types import SimpleNamespace

import pandas as pd

from v182.features import topdown_features as topdown


def test_clean_label_series_rejects_missing_and_non_text_values() -> None:
    values = pd.Series([" France ", float("nan"), None, 12.5, "nan", "N/A", "Germany"])
    cleaned = topdown._clean_label_series(values)
    assert cleaned.tolist() == ["France", "N/A", "N/A", "N/A", "N/A", "N/A", "Germany"]
    assert topdown._valid_group_labels(cleaned) == ["France", "Germany"]


def test_build_topdown_handles_mixed_country_and_sector_missing_values(monkeypatch) -> None:
    actions = pd.DataFrame(
        {
            "isin": ["FR0000000001", "FR0000000002", "DE0000000003", "NL0000000004"],
            "name": ["Alpha", "Beta", "Gamma", "Delta"],
            "country_yf": ["France", float("nan"), None, "Germany"],
            "sector_yf": ["Technology", float("nan"), 7.0, "Industrials"],
            "perf_1m_pct": [1.0, -1.0, 2.0, 0.5],
            "perf_6m_pct": [4.0, -2.0, 6.0, 3.0],
        }
    )
    etfs = pd.DataFrame(columns=["isin"])
    captured_queries: list[str] = []

    monkeypatch.setattr(
        topdown,
        "global_macro_score",
        lambda _key: SimpleNamespace(score=None, coverage=0.0, components={}, errors=[]),
    )

    def fake_score_queries(queries, **_kwargs):
        captured_queries.extend(list(queries))
        score = SimpleNamespace(article_count=1, positive_hits=1, negative_hits=0, score=50.0)
        return {query: (score, None) for query in queries}

    monkeypatch.setattr(topdown, "score_queries", fake_score_queries)

    result = topdown.build_topdown(actions, etfs, fred_api_key=None, instrument_news_top_n=0)

    country_queries = {query for query in captured_queries if "economy OR markets OR rates OR inflation" in query}
    sector_queries = {query for query in captured_queries if "stocks OR industry OR earnings OR outlook" in query}

    assert country_queries == {
        '"France" (economy OR markets OR rates OR inflation)',
        '"Germany" (economy OR markets OR rates OR inflation)',
    }
    assert sector_queries == {
        '"Technology" (stocks OR industry OR earnings OR outlook)',
        '"Industrials" (stocks OR industry OR earnings OR outlook)',
    }
    assert all('"nan"' not in query.lower() for query in captured_queries)
    assert all('"7.0"' not in query for query in captured_queries)
    assert set(result.action_scores) == set(actions["isin"])
