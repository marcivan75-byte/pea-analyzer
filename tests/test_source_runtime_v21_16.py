from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd

from v182.reporting.boursorama_shadow_run import _collect as broad_shadow_collect
from v182.reporting.selected_source_enrichment import (
    _append_source_metadata,
    _investing_budgeted_rows,
    _migrate_cache_version,
    _pivot,
    select_preselected_rows,
)


def test_action_cache_v1_to_v2_migration_preserves_entries(tmp_path: Path):
    path = tmp_path / "cache.json"
    original_entry = {
        "status": "OK",
        "fields": {"boursorama_consensus": 4.3},
        "dynamic_fetched_at_utc": "2026-08-22T20:00:00+00:00",
    }
    path.write_text(json.dumps({"version": "BOURSORAMA_SELECTED_V1", "entries": {"FR1": original_entry}}), encoding="utf-8")
    assert _migrate_cache_version(path, old_version="BOURSORAMA_SELECTED_V1", new_version="BOURSORAMA_SELECTED_V2") is True
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == "BOURSORAMA_SELECTED_V2"
    assert migrated["entries"]["FR1"] == original_entry


def test_source_budget_prioritizes_buy_before_higher_scored_watch():
    rows = pd.DataFrame([
        {"isin": "WATCH_HIGH", "decision": "WATCH", "score": 99.0},
        {"isin": "BUY_LOWER", "decision": "BUY_CANDIDATE", "score": 75.0},
        {"isin": "REVIEW_HIGH", "decision": "REVIEW", "score": 100.0},
    ])
    selected = select_preselected_rows(rows, max_unique_instruments=1)
    assert list(selected["isin"].unique()) == ["BUY_LOWER"]


def test_investing_resolution_budget_never_excludes_already_known_isins(tmp_path: Path):
    cache_dir = tmp_path / "state" / "provenance" / "source_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "INVESTING_URL_MAP_V1.json").write_text(
        json.dumps({"version": "INVESTING_URL_MAP_V1", "entries": {"KNOWN_MAP": {"status": "RESOLVED", "base_url": "https://www.investing.com/equities/test"}}}),
        encoding="utf-8",
    )
    (cache_dir / "INVESTING_TECHNICAL_V1.json").write_text(
        json.dumps({"version": "INVESTING_TECHNICAL_V1", "entries": {"KNOWN_CACHE": {"fields": {}}}}),
        encoding="utf-8",
    )
    rows = pd.DataFrame([{"isin": value} for value in ["KNOWN_MAP", "KNOWN_CACHE", "NEW1", "NEW2", "NEW3"]])
    selected, deferred, cooldown = _investing_budgeted_rows(rows, tmp_path, 2)
    assert set(selected["isin"]) == {"KNOWN_MAP", "KNOWN_CACHE", "NEW1", "NEW2"}
    assert deferred == 1
    assert cooldown == 0


def test_investing_new_url_resolution_slots_follow_decision_priority_not_input_order(tmp_path: Path):
    cache_dir = tmp_path / "state" / "provenance" / "source_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "INVESTING_URL_MAP_V1.json").write_text(
        json.dumps({"version": "INVESTING_URL_MAP_V1", "entries": {"KNOWN": {"status": "RESOLVED", "base_url": "https://www.investing.com/equities/known"}}}),
        encoding="utf-8",
    )
    rows = pd.DataFrame([
        {"isin": "NEW_REVIEW", "decision": "REVIEW", "score": 99.0},
        {"isin": "KNOWN", "decision": "WATCH", "score": 50.0},
        {"isin": "NEW_T1", "decision": "T1_STARTER_25_SHADOW", "score": 82.0},
        {"isin": "NEW_T2", "decision": "T2_CONFIRM_75_SHADOW", "score": 80.0},
        {"isin": "NEW_BUY", "decision": "BUY_CANDIDATE", "score": 76.0},
    ])
    selected, deferred, cooldown = _investing_budgeted_rows(rows, tmp_path, 2)
    assert set(selected["isin"]) == {"KNOWN", "NEW_BUY", "NEW_T2"}
    assert list(selected[selected["isin"] != "KNOWN"]["isin"].unique()) == ["NEW_BUY", "NEW_T2"]
    assert deferred == 2
    assert cooldown == 0


def test_investing_cooldown_releases_slot_to_next_priority_candidate(tmp_path: Path):
    cache_dir = tmp_path / "state" / "provenance" / "source_cache"
    cache_dir.mkdir(parents=True)
    now = datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)
    (cache_dir / "INVESTING_URL_MAP_V1.json").write_text(
        json.dumps({
            "version": "INVESTING_URL_MAP_V1",
            "entries": {
                "BUY_COOLDOWN": {
                    "status": "UNRESOLVED",
                    "last_failed_at_utc": (now - timedelta(hours=2)).isoformat(),
                    "reason": "NO_VALIDATED_PUBLIC_URL",
                }
            },
        }),
        encoding="utf-8",
    )
    rows = pd.DataFrame([
        {"isin": "BUY_COOLDOWN", "decision": "BUY_CANDIDATE", "score": 95.0},
        {"isin": "T2_NEXT", "decision": "T2_CONFIRM_75_SHADOW", "score": 80.0},
        {"isin": "WATCH_LATER", "decision": "WATCH", "score": 99.0},
    ])
    selected, deferred, cooldown = _investing_budgeted_rows(
        rows,
        tmp_path,
        1,
        unmapped_retry_ttl_hours=24,
        now=now,
    )
    assert list(selected["isin"].unique()) == ["T2_NEXT"]
    assert deferred == 1
    assert cooldown == 1


def test_investing_expired_cooldown_becomes_retry_eligible_in_priority_order(tmp_path: Path):
    cache_dir = tmp_path / "state" / "provenance" / "source_cache"
    cache_dir.mkdir(parents=True)
    now = datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)
    (cache_dir / "INVESTING_URL_MAP_V1.json").write_text(
        json.dumps({
            "version": "INVESTING_URL_MAP_V1",
            "entries": {
                "BUY_RETRY": {
                    "status": "UNRESOLVED",
                    "last_failed_at_utc": (now - timedelta(hours=25)).isoformat(),
                }
            },
        }),
        encoding="utf-8",
    )
    rows = pd.DataFrame([
        {"isin": "WATCH", "decision": "WATCH", "score": 99.0},
        {"isin": "BUY_RETRY", "decision": "BUY_CANDIDATE", "score": 70.0},
    ])
    selected, deferred, cooldown = _investing_budgeted_rows(rows, tmp_path, 1, unmapped_retry_ttl_hours=24, now=now)
    assert list(selected["isin"].unique()) == ["BUY_RETRY"]
    assert deferred == 1
    assert cooldown == 0


def test_source_metadata_uses_factual_collection_time_not_synthetic_age_timestamp():
    observations = [
        {"isin": "FR1", "asset_class": "ACTION", "horizon": "CT", "field": "boursorama_consensus", "value": 4.2, "source": "Boursorama public priority fiche", "source_url": "https://example.test/fact", "collected_at": "2026-08-22T18:00:00+00:00", "validation_status": "POST_SELECTION_PRIORITY_CONTEXT"},
        {"isin": "FR1", "asset_class": "ACTION", "horizon": "CT", "field": "boursorama_dynamic_age_hours", "value": 2.0, "source": "Boursorama cache metadata", "source_url": "https://example.test/meta", "collected_at": "2026-08-22T20:00:00+00:00", "validation_status": "SOURCE_FRESHNESS_METADATA"},
    ]
    enriched = _append_source_metadata(observations)
    latest = next(row for row in enriched if row["field"] == "boursorama_latest_collected_at")
    urls = next(row for row in enriched if row["field"] == "boursorama_source_urls")
    assert latest["value"] == "2026-08-22T18:00:00+00:00"
    assert urls["value"] == "https://example.test/fact"


def test_observation_pivot_is_one_row_per_asset_horizon_isin():
    observations = [
        {"isin": "FR1", "asset_class": "ACTION", "horizon": "CT", "field": "a", "value": 1},
        {"isin": "FR1", "asset_class": "ACTION", "horizon": "CT", "field": "b", "value": 2},
        {"isin": "FR1", "asset_class": "ACTION", "horizon": "MT", "field": "a", "value": 3},
    ]
    pivoted = _pivot(observations)
    assert len(pivoted) == 2
    assert not pivoted.duplicated(["isin", "asset_class", "horizon"]).any()


def test_broad_boursorama_shadow_is_disabled_by_default_without_network(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("PEA_BOURSORAMA_SHADOW_AUDIT", raising=False)
    rows = pd.DataFrame([{"isin": "FR1", "yahoo_ticker": "AI.PA"}])
    result = broad_shadow_collect(rows, tmp_path, profile="WEEKLY_FULL_COMMITTEE", actions_input="TEST")
    assert result["status"] == "DISABLED_V21_16_SELECTED_ONLY_ARCHITECTURE"
    assert result["network_collection_executed"] is False
    assert result["live_refresh_requested"] == 0
    assert result["decision_influence"] is False
