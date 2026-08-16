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
    queries: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        topdown,
        "global_macro_score",
        lambda _key: SimpleNamespace(score=None, coverage=0.0, components={}, errors=[]),
    )

    def fake_query(query: str, diagnostics: list[dict], kind: str, key: str) -> float:
        queries.append((kind, key, query))
        return 50.0

    monkeypatch.setattr(topdown, "_query_score", fake_query)

    result = topdown.build_topdown(actions, etfs, fred_api_key=None, instrument_news_top_n=0)

    country_keys = {key for kind, key, _ in queries if kind == "ACTION_country_news"}
    sector_keys = {key for kind, key, _ in queries if kind == "ACTION_sector_news"}
    assert country_keys == {"France", "Germany"}
    assert sector_keys == {"Technology", "Industrials"}
    assert "nan" not in {str(key).lower() for _, key, _ in queries}
    assert 7.0 not in sector_keys
    assert set(result.action_scores) == set(actions["isin"])
