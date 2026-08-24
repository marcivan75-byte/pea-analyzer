from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json

import pandas as pd

from v182.features import topdown_features as topdown_base
from v182.features import topdown_prefetch_v21_15_4 as topdown_prefetch
from v182.reporting import daily_fast_collection_run as fast
from v182.sources import finnhub_consensus, yfinance_info
from v182.sources.gdelt_news import NewsScore


def _frame(prefix: str, rows: int = 3, *, equal_caps: bool = False) -> pd.DataFrame:
    caps = [100] * rows if equal_caps else [100 + i for i in range(rows)]
    return pd.DataFrame(
        {
            "isin": [f"{prefix}{i}" for i in range(rows)],
            "name": [f"Name {i}" for i in range(rows)],
            "market_cap": caps,
            "perf_1m_pct": [1.0 + i for i in range(rows)],
            "perf_6m_pct": [2.0 + i for i in range(rows)],
            "country_yf": ["France"] * rows,
            "sector_yf": ["Technology"] * rows,
            "unrelated_field": ["x"] * rows,
        }
    )


def _fake_external(prepared: topdown_prefetch.PreparedTopdown):
    macro = SimpleNamespace(score=60.0, coverage=1.0, components={"x": 60.0}, errors=[])
    results = {
        spec["query"]: (NewsScore(60.0, 3, 2, 1, "GDELT"), None)
        for spec in prepared.specs
    }
    return macro, results


def _install_without_network(runtime: fast.DailyFastRuntime, monkeypatch) -> None:
    def fake_fetch(prepared, *, fred_api_key):
        macro, results = _fake_external(prepared)
        return topdown_prefetch.ExternalTopdown(
            macro=macro,
            news_results=results,
            query_fingerprint=prepared.query_fingerprint,
        )

    monkeypatch.setattr(topdown_prefetch, "fetch_external", fake_fetch)
    runtime.install()


def test_topdown_query_fingerprint_ignores_local_perf_but_tracks_query_inputs() -> None:
    actions = _frame("FR")
    etfs = _frame("ETF", 2)
    baseline = topdown_prefetch.prepare(actions, etfs, instrument_news_top_n=2)

    perf_only = actions.copy()
    perf_only["perf_1m_pct"] = 999.0
    assert topdown_prefetch.prepare(perf_only, etfs, instrument_news_top_n=2).query_fingerprint == baseline.query_fingerprint

    ranking_change = actions.copy()
    ranking_change.loc[0, "market_cap"] = 9999
    assert topdown_prefetch.prepare(ranking_change, etfs, instrument_news_top_n=2).query_fingerprint != baseline.query_fingerprint


def test_topdown_query_fingerprint_is_order_sensitive_when_topn_tie_can_change_selection() -> None:
    actions = _frame("FR", 3, equal_caps=True)
    etfs = _frame("ETF", 2)
    first = topdown_prefetch.prepare(actions, etfs, instrument_news_top_n=2)
    reordered = actions.iloc[::-1].reset_index(drop=True)
    second = topdown_prefetch.prepare(reordered, etfs, instrument_news_top_n=2)
    assert first.query_fingerprint != second.query_fingerprint


def test_prefetched_topdown_finalization_matches_legacy_formula(monkeypatch) -> None:
    actions = _frame("FR", 3)
    etfs = _frame("ETF", 2)
    prepared = topdown_prefetch.prepare(actions, etfs, instrument_news_top_n=2)
    macro, results = _fake_external(prepared)
    external = topdown_prefetch.ExternalTopdown(
        macro=macro,
        news_results=results,
        query_fingerprint=prepared.query_fingerprint,
    )

    monkeypatch.setattr(topdown_base, "global_macro_score", lambda _key: macro)
    monkeypatch.setattr(topdown_base, "score_queries", lambda *args, **kwargs: results)
    legacy_result = topdown_base.build_topdown(actions, etfs, fred_api_key=None, instrument_news_top_n=2)
    fast_result = topdown_prefetch.finalize(actions, etfs, prepared, external)

    assert fast_result.global_scores == legacy_result.global_scores
    assert fast_result.action_scores == legacy_result.action_scores
    assert fast_result.etf_scores == legacy_result.etf_scores
    assert fast_result.provenance == legacy_result.provenance


def test_fast_frame_requires_exact_unique_row_count() -> None:
    good = pd.DataFrame({"isin": ["A", "B"]})
    assert fast._valid_fast_frame(good, expected_rows=2) is True
    assert fast._valid_fast_frame(good, expected_rows=3) is False
    duplicate = pd.DataFrame({"isin": ["A", "A"]})
    assert fast._valid_fast_frame(duplicate, expected_rows=2) is False


def test_dedupe_dicts_is_stable() -> None:
    rows = [{"a": 1, "b": 2}, {"b": 2, "a": 1}, {"a": 2}]
    assert fast._dedupe_dicts(rows) == [{"a": 1, "b": 2}, {"a": 2}]


def test_delta_mode_keeps_cached_eps_book_for_daily_price_ratios_and_drops_other_yahoo_cache(monkeypatch) -> None:
    runtime = fast.DailyFastRuntime(
        _frame("FR"),
        _frame("ETF", 2),
        {},
        "DELTA_ONLY",
        datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )
    _install_without_network(runtime, monkeypatch)
    try:
        entry = {
            "fetched_at_utc": "2026-08-22T20:00:00+00:00",
            "observations": [
                {"field": "forward_eps_yf", "value": 10.0},
                {"field": "book_value_per_share_yf", "value": 20.0},
                {"field": "sector_yf", "value": "Technology"},
            ],
        }
        rows = yfinance_info._entry_rows(entry, "ABC.PA", "CACHE_HIT", "HOT")
        assert {row["field"] for row in rows} == {"forward_eps_yf", "book_value_per_share_yf"}
        live = yfinance_info._entry_rows(entry, "ABC.PA", "LIVE_REFRESH", "HOT")
        assert {row["field"] for row in live} == {"forward_eps_yf", "book_value_per_share_yf", "sector_yf"}
    finally:
        runtime.restore()


def test_delta_mode_emits_only_live_finnhub_field_groups(monkeypatch) -> None:
    runtime = fast.DailyFastRuntime(
        _frame("FR"),
        _frame("ETF", 2),
        {},
        "DELTA_ONLY",
        datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )
    _install_without_network(runtime, monkeypatch)
    try:
        entry = {
            "status": "OK",
            "recommendation_fetched_at_utc": "2026-08-22T20:00:00+00:00",
            "target_fetched_at_utc": "2026-08-22T20:00:00+00:00",
            "observations": [
                {"field": "consensus", "value": "BUY", "group": "RECOMMENDATION"},
                {"field": "target_price", "value": 120.0, "group": "TARGET"},
            ],
        }
        cached = finnhub_consensus._entry_observations(
            entry,
            "ABC.PA",
            recommendation_live=False,
            target_live=False,
        )
        assert cached == []
        target_only = finnhub_consensus._entry_observations(
            entry,
            "ABC.PA",
            recommendation_live=False,
            target_live=True,
        )
        assert [row["field"] for row in target_only] == ["target_price"]
    finally:
        runtime.restore()


def test_fast_state_static_contract_mismatch_disables_reuse(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state"
    state.mkdir()
    manifest_path = state / "manifest.json"
    actions_path = state / "actions.parquet"
    etf_path = state / "etf.parquet"
    monkeypatch.setattr(fast, "MANIFEST", manifest_path)
    monkeypatch.setattr(fast, "ACTIONS_STATE", actions_path)
    monkeypatch.setattr(fast, "ETF_STATE", etf_path)
    monkeypatch.setenv("PEA_RUN_PROFILE", "DAILY_TACTICAL")
    monkeypatch.setattr(fast, "_static_contract", lambda: {"contract": "CURRENT"})

    _frame("FR").to_parquet(actions_path, index=False)
    _frame("ETF", 2).to_parquet(etf_path, index=False)
    manifest_path.write_text(
        json.dumps(
            {
                "version": fast.VERSION,
                "validated": True,
                "static_contract": {"contract": "OLD"},
                "actions_rows": 3,
                "etf_rows": 2,
            }
        ),
        encoding="utf-8",
    )
    _actions, _etf, _manifest, mode = fast._load_fast_state()
    assert mode == "DISABLED"


def test_source_cache_change_uses_reconciliation_not_stale_delta(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state"
    state.mkdir()
    manifest_path = state / "manifest.json"
    actions_path = state / "actions.parquet"
    etf_path = state / "etf.parquet"
    monkeypatch.setattr(fast, "MANIFEST", manifest_path)
    monkeypatch.setattr(fast, "ACTIONS_STATE", actions_path)
    monkeypatch.setattr(fast, "ETF_STATE", etf_path)
    monkeypatch.setenv("PEA_RUN_PROFILE", "DAILY_TACTICAL")
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setattr(fast, "_static_contract", lambda: {"contract": "SAME"})
    monkeypatch.setattr(fast, "_cache_contract", lambda: {"cache": "NEW"})

    actions = _frame("FR")
    etfs = _frame("ETF", 2)
    actions.to_parquet(actions_path, index=False)
    etfs.to_parquet(etf_path, index=False)
    manifest_path.write_text(
        json.dumps(
            {
                "version": fast.VERSION,
                "validated": True,
                "static_contract": {"contract": "SAME"},
                "cache_contract": {"cache": "OLD"},
                "actions_rows": 3,
                "etf_rows": 2,
                "actions_sha256": fast._sha256_file(actions_path),
                "etf_sha256": fast._sha256_file(etf_path),
            }
        ),
        encoding="utf-8",
    )
    _actions, _etf, _manifest, mode = fast._load_fast_state()
    assert mode == "RECONCILE_CACHE"


def test_promotion_requires_both_quality_passed_captured_masters() -> None:
    runtime = fast.DailyFastRuntime(
        pd.DataFrame(),
        pd.DataFrame(),
        {},
        "DISABLED",
        datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )
    assert runtime.promote() == {"promoted": False, "reason": "ENRICHED_MASTER_NOT_CAPTURED"}


def test_launcher_promotes_only_after_legacy_pipeline_returns() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "v182" / "reporting" / "daily_fast_collection_run.py").read_text(encoding="utf-8")
    assert source.index("result = legacy._run_pipeline") < source.index("promotion = fast.promote()")
    assert '"full_pipeline_fallback_when_state_invalid": True' in source
    assert '"decision_logic_changed": False' in source
    assert '"criteria_changed": False' in source
    assert '"weights_changed": False' in source
    assert '"thresholds_changed": False' in source
