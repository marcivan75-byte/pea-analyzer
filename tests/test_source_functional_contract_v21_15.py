from pathlib import Path
import json

import pandas as pd

from v182.reporting.selected_source_enrichment import select_preselected_rows

ROOT = Path(__file__).resolve().parents[1]


def test_source_contract_locks_previous_validated_functions():
    cfg = json.loads((ROOT / "config" / "SOURCE_FUNCTIONAL_CONTRACT_V21_15.json").read_text(encoding="utf-8"))
    assert cfg["boursorama"]["priority_for_selected_actions"] is True
    assert cfg["boursorama"]["priority_for_selected_etfs"] is True
    assert cfg["boursorama"]["full_universe_daily_scrape_forbidden"] is True
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
