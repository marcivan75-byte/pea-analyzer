from pathlib import Path
from types import SimpleNamespace
import json
import threading
import time

import pandas as pd

from v182.reporting import selected_source_enrichment as source_enrichment
from v182.reporting.selected_source_enrichment import select_preselected_rows

ROOT = Path(__file__).resolve().parents[1]


def test_source_contract_locks_previous_validated_functions():
    cfg = json.loads((ROOT / "config" / "SOURCE_FUNCTIONAL_CONTRACT_V21_15.json").read_text(encoding="utf-8"))
    assert cfg["version"] == "V21.15.2"
    assert cfg["boursorama"]["priority_for_selected_actions"] is True
    assert cfg["boursorama"]["priority_for_selected_etfs"] is True
    assert cfg["boursorama"]["full_universe_daily_scrape_forbidden"] is True
    assert cfg["boursorama"]["asset_branches_overlap_under_shared_limiter"] is True
    assert cfg["boursorama"]["request_start_interval_seconds"] == 1.0
    assert "replication_management_fee" in cfg["boursorama"]["required_etf_context_families"]
    assert cfg["investing"]["timeframes"] == ["DAILY", "WEEKLY", "MONTHLY"]
    assert cfg["investing"]["allowed_states"] == ["STRONG_SELL", "SELL", "NEUTRAL", "BUY", "STRONG_BUY"]
    assert cfg["investing"]["horizon_mapping"] == {"TCT": "DAILY", "CT": "WEEKLY", "MT": "MONTHLY"}
    assert cfg["governance"]["changes_reference_scores"] is False
    assert cfg["governance"]["silent_function_removal_forbidden"] is True


def test_preselection_layer_cannot_create_candidates_and_is_bounded():
    rows = pd.DataFrame(
        [
            {"isin": "A", "decision": "BUY_CANDIDATE", "score": 90},
            {"isin": "B", "decision": "WATCH", "score": 80},
            {"isin": "C", "decision": "NO_ACTION", "score": 99},
            {"isin": "D", "decision": "REVIEW", "score": 70},
        ]
    )
    selected = select_preselected_rows(rows, max_unique_instruments=2)
    assert set(selected["isin"]) == {"A", "B"}
    assert "C" not in set(selected["isin"])


def test_all_active_horizon_runners_keep_source_context_hook():
    daily = (ROOT / "src" / "v182" / "reporting" / "daily_tct_ct_runner.py").read_text(encoding="utf-8")
    action_mt = (ROOT / "src" / "v182" / "reporting" / "action_mt_shadow_run_v1.py").read_text(encoding="utf-8")
    etf_mt = (ROOT / "src" / "v182" / "reporting" / "etf_mt_v2081_run.py").read_text(encoding="utf-8")
    orchestrator = (ROOT / "src" / "v182" / "reporting" / "selected_source_enrichment.py").read_text(encoding="utf-8")
    assert "enrich_selected_rows" in daily and 'profile="DAILY_TCT_CT"' in daily
    assert "enrich_selected_rows" in action_mt and 'profile="ACTION_MT"' in action_mt
    assert "enrich_selected_rows" in etf_mt and 'profile="ETF_MT"' in etf_mt
    assert "collect_selected_action_context_cached" in orchestrator
    assert "collect_selected_etf_context_cached" in orchestrator
    assert "collect_technical_context_cached" in orchestrator
    assert "shared_limiter = StartRateLimiter" in orchestrator
    assert "fetcher=shared_fetcher" in orchestrator
    assert orchestrator.count("request_start_interval_seconds=0.0") == 2
    assert 'thread_name_prefix="boursorama-assets"' in orchestrator


def test_action_etf_boursorama_overlap_keeps_one_provider_start_cadence(tmp_path, monkeypatch):
    interval = 0.03
    contract = {
        "version": "V21.15.2",
        "scope": {
            "selected_only_max_unique_instruments": 40,
            "preselection_statuses": ["BUY_CANDIDATE", "WATCH", "REVIEW", "SHADOW_CANDIDATE"],
        },
        "boursorama": {
            "priority_for_selected_actions": True,
            "priority_for_selected_etfs": True,
            "dynamic_ttl_hours": 8,
            "deep_ttl_hours": 168,
            "refresh_budget": 40,
            "request_start_interval_seconds": interval,
            "max_workers": 4,
        },
        "investing": {
            "refresh_budget": 40,
            "ttl_hours": 6,
            "request_start_interval_seconds": 0.0,
            "max_workers": 1,
        },
    }
    monkeypatch.setattr(source_enrichment, "_read_contract", lambda root: contract)

    starts = []
    starts_lock = threading.Lock()

    class FakeResponse:
        text = "ok"

        def raise_for_status(self):
            return None

    def fake_get(url, *, headers, timeout):
        with starts_lock:
            starts.append((url, time.monotonic()))
        return FakeResponse()

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    branch_barrier = threading.Barrier(2)
    seen_fetchers = []

    def fake_boursorama(rows, cache_path, **kwargs):
        assert kwargs["request_start_interval_seconds"] == 0.0
        fetcher = kwargs["fetcher"]
        seen_fetchers.append(fetcher)
        branch_barrier.wait(timeout=1.0)
        fetcher(f"https://example.test/{rows.iloc[0]['asset_class'].lower()}", timeout=1.0)
        return SimpleNamespace(observations=[], failures=[], metrics={"status": "OK"})

    monkeypatch.setattr(source_enrichment, "collect_selected_action_context_cached", fake_boursorama)
    monkeypatch.setattr(source_enrichment, "collect_selected_etf_context_cached", fake_boursorama)
    monkeypatch.setattr(
        source_enrichment,
        "collect_technical_context_cached",
        lambda *args, **kwargs: SimpleNamespace(observations=[], failures=[], metrics={"status": "OK"}),
    )

    rows = pd.DataFrame(
        [
            {"isin": "A", "asset_class": "ACTION", "horizon": "CT", "decision": "WATCH", "score": 90},
            {"isin": "E", "asset_class": "ETF", "horizon": "MT", "decision": "WATCH", "score": 88},
        ]
    )
    _enriched, payload = source_enrichment.enrich_selected_rows(rows, tmp_path, profile="TEST")

    assert len(seen_fetchers) == 2
    assert seen_fetchers[0] is seen_fetchers[1]
    assert len(starts) == 2
    ordered = sorted(timestamp for _url, timestamp in starts)
    assert ordered[1] - ordered[0] >= interval * 0.8
    assert payload["boursorama_asset_overlap"] is True
    assert payload["boursorama_shared_start_limiter"] is True
