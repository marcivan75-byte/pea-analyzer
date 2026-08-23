from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.reporting import daily_fast_collection_run as fast
from v182.sources import finnhub_consensus, yfinance_info


def _frame(prefix: str, rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "isin": [f"{prefix}{i}" for i in range(rows)],
            "name": [f"Name {i}" for i in range(rows)],
            "market_cap": [100 + i for i in range(rows)],
            "perf_1m_pct": [1.0 + i for i in range(rows)],
            "perf_6m_pct": [2.0 + i for i in range(rows)],
            "country_yf": ["France"] * rows,
            "sector_yf": ["Technology"] * rows,
            "unrelated_field": ["x"] * rows,
        }
    )


def test_topdown_fingerprint_tracks_only_functional_inputs() -> None:
    actions = _frame("FR")
    etfs = _frame("ETF", 2)
    baseline = fast._topdown_fingerprint(actions, etfs)

    irrelevant = actions.copy()
    irrelevant["unrelated_field"] = "changed"
    assert fast._topdown_fingerprint(irrelevant, etfs) == baseline

    relevant = actions.copy()
    relevant.loc[0, "market_cap"] = 999
    assert fast._topdown_fingerprint(relevant, etfs) != baseline


def test_topdown_fingerprint_is_fail_closed_on_row_order_change() -> None:
    actions = _frame("FR")
    etfs = _frame("ETF", 2)
    reordered = actions.iloc[::-1].reset_index(drop=True)
    # build_topdown can use stable input order to resolve a top-N tie, therefore
    # prefetch reuse must be rejected if the actual row order changed.
    assert fast._topdown_fingerprint(actions, etfs) != fast._topdown_fingerprint(reordered, etfs)


def test_fast_frame_requires_exact_unique_row_count() -> None:
    good = pd.DataFrame({"isin": ["A", "B"]})
    assert fast._valid_fast_frame(good, expected_rows=2) is True
    assert fast._valid_fast_frame(good, expected_rows=3) is False
    duplicate = pd.DataFrame({"isin": ["A", "A"]})
    assert fast._valid_fast_frame(duplicate, expected_rows=2) is False


def test_dedupe_dicts_is_stable() -> None:
    rows = [{"a": 1, "b": 2}, {"b": 2, "a": 1}, {"a": 2}]
    assert fast._dedupe_dicts(rows) == [{"a": 1, "b": 2}, {"a": 2}]


def test_delta_mode_keeps_cached_eps_book_for_daily_price_ratios_and_drops_other_yahoo_cache() -> None:
    runtime = fast.DailyFastRuntime(
        _frame("FR"),
        _frame("ETF", 2),
        {},
        "DELTA_ONLY",
        datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )
    runtime.install()
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


def test_delta_mode_emits_only_live_finnhub_field_groups() -> None:
    runtime = fast.DailyFastRuntime(
        _frame("FR"),
        _frame("ETF", 2),
        {},
        "DELTA_ONLY",
        datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
    )
    runtime.install()
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

    _frame("FR").to_parquet(actions_path, index=False)
    _frame("ETF", 2).to_parquet(etf_path, index=False)
    manifest_path.write_text(
        json.dumps(
            {
                "version": fast.VERSION,
                "validated": True,
                "static_contract": {"contract": "SAME"},
                "cache_contract": {"cache": "OLD"},
                "actions_rows": 3,
                "etf_rows": 2,
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
