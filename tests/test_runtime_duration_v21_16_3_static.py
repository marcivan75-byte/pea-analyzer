from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting.daily_tct_ct_runner import _compact_tct_baseline, _daily_exact_scope
from v182.reporting.runtime_telemetry import _budget_for_profile


ROOT = Path(__file__).resolve().parents[1]


def _requirements(path: str) -> set[str]:
    rows = set()
    for raw in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("==", 1)[0].split(">=", 1)[0].split("<=", 1)[0].split("~=", 1)[0].strip().lower()
        rows.add(name)
    return rows


def test_runtime_contract_v21_16_3_is_loaded_by_telemetry() -> None:
    daily = _budget_for_profile("DAILY_TACTICAL")
    weekly = _budget_for_profile("WEEKLY_FULL_COMMITTEE")
    assert daily["contract_version"] == "RUNTIME_DURATION_CONTRACT_V21_16_3"
    assert daily["expected_wall_range_minutes"] == [3.8, 5.5]
    assert daily["billable_budget_minutes"] == 6
    assert daily["alert_wall_minutes"] == 7.0
    assert weekly["expected_wall_range_minutes"] == [15.5, 18.5]
    assert weekly["billable_budget_minutes"] == 19
    assert weekly["alert_wall_minutes"] == 22.0
    assert daily["targets_are_not_observed_runtime"] is True
    assert weekly["targets_are_not_observed_runtime"] is True


def test_legacy_runtime_file_names_remain_compatible() -> None:
    telemetry = (ROOT / "src/v182/reporting/runtime_telemetry.py").read_text(encoding="utf-8")
    unified = (ROOT / "src/v182/reporting/unified_runner.py").read_text(encoding="utf-8")
    assert 'RUNTIME_VERSION = "PIPELINE_RUNTIME_V21_13_7"' in telemetry
    assert 'root / "UNIFIED_RUNTIME_V21_13_7.json"' in telemetry
    assert 'root / "UNIFIED_RUNTIME_V21_13_7.csv"' in telemetry
    assert '"outputs/audit/UNIFIED_RUNTIME_V21_13_7.json"' in unified
    assert '"outputs/audit/UNIFIED_RUNTIME_V21_13_7.csv"' in unified


def test_daily_dependency_profile_is_strict_subset_without_document_browser_stack() -> None:
    full = _requirements("requirements-runtime.txt")
    daily = _requirements("requirements-daily-fast.txt")
    constraints = _requirements("constraints-ci.txt")
    assert daily < full
    assert daily <= constraints
    assert {"pypdf", "playwright", "openpyxl", "python-docx"}.isdisjoint(daily)
    assert {"pandas", "numpy", "pyarrow", "yfinance", "requests", "beautifulsoup4", "lxml", "ta"} <= daily


def test_weekly_dependency_profile_retains_ci_document_builders_only() -> None:
    full = _requirements("requirements-runtime.txt")
    weekly = _requirements("requirements-market-runtime.txt")
    constraints = _requirements("constraints-ci.txt")
    assert weekly < full
    assert weekly <= constraints
    assert {"pypdf", "playwright"}.isdisjoint(weekly)
    assert {"openpyxl", "python-docx"} <= weekly


def test_workflows_use_lean_dependency_profiles() -> None:
    daily = (ROOT / ".github/workflows/committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    weekly = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    assert "requirements-daily-fast.txt" in daily
    assert "requirements-runtime.txt" not in daily
    assert "requirements-market-runtime.txt" in weekly
    assert "requirements-runtime.txt" not in weekly


def test_daily_t1_t2_exact_scope_preserves_full_baseline_gate_and_caps_to_top20() -> None:
    frame = pd.DataFrame(
        {
            "isin": [f"X{i:04d}" for i in range(1, 41)],
            "tct_baseline_rank": list(range(1, 41)),
            "tct_baseline_coverage": [0.9] * 19 + [0.59] + [0.9] * 20,
        }
    )
    cfg = {"scope": {"baseline_top_n": 20, "baseline_min_coverage": 0.60}}
    scoped = _daily_exact_scope(frame, cfg)
    assert len(scoped) == 19
    assert scoped["tct_baseline_rank"].max() == 19
    assert set(scoped["isin"]) == set(frame.loc[:18, "isin"])


def test_compact_daily_tct_baseline_keeps_every_reconstruction_field() -> None:
    frame = pd.DataFrame(
        {
            "isin": ["FR0001"],
            "name": ["A"],
            "last_close": [100.0],
            "market_cap": [1e9],
            "tct_baseline_score": [81.0],
            "tct_baseline_rank": [1],
            "tct_baseline_component_squeeze": [90.0],
            "tct_baseline_component_squeeze_observed": [True],
            "tct_baseline_missing_weight_policy": ["RENORM"],
        }
    )
    compact = _compact_tct_baseline(frame)
    assert "isin" in compact
    assert "name" in compact
    assert "tct_baseline_score" in compact
    assert "tct_baseline_component_squeeze" in compact
    assert "tct_baseline_component_squeeze_observed" in compact
    assert "tct_baseline_missing_weight_policy" in compact
    assert "last_close" not in compact
    assert "market_cap" not in compact


def test_daily_workflow_keeps_full_screening_and_defers_only_research_validation() -> None:
    collector = (ROOT / "src/v182/reporting/daily_fast_collection.py").read_text(encoding="utf-8")
    postmarket = (ROOT / "src/v182/reporting/tct_postmarket_bundle_run.py").read_text(encoding="utf-8")
    assert '"universe_reduced": False' in collector
    assert "WAVE_04_ACTION_FUNDAMENTALS" in collector
    assert "heavy_slow_source_waves_skipped" in collector
    assert 'deferred = ["PIT_LINEAGE_V24.4.2", "PIT_VALIDATOR_V24.4.2"]' in postmarket
    assert 'daily_deferred_steps_have_decision_influence": False' in postmarket


def test_daily_bundle_uses_memory_handoff_and_keeps_weekly_baseline_immutable() -> None:
    bundle = (ROOT / "src/v182/reporting/daily_fast_bundle_v21_16.py").read_text(encoding="utf-8")
    collector = (ROOT / "src/v182/reporting/daily_fast_collection.py").read_text(encoding="utf-8")
    assert "persist_masters=False" in bundle
    assert "persist_daily_baseline=False" in bundle
    assert "return_frames=True" in bundle
    assert "actions=actions" in bundle and "etfs=etfs" in bundle
    assert "persist_full_baseline=False" in bundle
    assert '"full_master_csv_roundtrip_avoided": True' in bundle
    assert '"weekly_baseline_parquet_rewrite_avoided": True' in bundle
    assert "weekly_baseline_kept_immutable_in_scheduled_bundle" in collector


def test_catalyst_overlaps_independent_news_and_global_market_only() -> None:
    source = (ROOT / "src/v182/reporting/tct_next_session_catalyst_engine.py").read_text(encoding="utf-8")
    assert "ThreadPoolExecutor(max_workers=2" in source
    assert "news_future = pool.submit" in source
    assert "market_future = pool.submit" in source
    assert '"provider_start_policy_changed": False' in source


def test_daily_source_prewarm_is_bounded_nonblocking_and_does_not_replace_current_gate() -> None:
    helper = (ROOT / "src/v182/reporting/daily_source_prewarm_v21_16.py").read_text(encoding="utf-8")
    bundle = (ROOT / "src/v182/reporting/daily_fast_bundle_v21_16.py").read_text(encoding="utf-8")
    friday = (ROOT / "src/v182/reporting/friday_tactical_reuse_v21_16.py").read_text(encoding="utf-8")
    assert "MAX_PREWARM = 20" in helper
    assert "current_gate_still_completes_new_candidates" in helper
    assert "prewarm_future = pool.submit" in bundle
    assert '"prewarm_failure_nonblocking": True' in bundle
    assert '"current_source_gate_coverage_reduced": False' in bundle
    assert "persist_seed(governed, root)" in friday


def test_weekly_topdown_hides_only_local_nondependent_stages() -> None:
    source = (ROOT / "src/v182/reporting/weekly_enrichment_fast_v21_16.py").read_text(encoding="utf-8")
    assert 'pending["rotation"] = local_pool.submit(original_rotation' in source
    assert 'pending["action_enhancements"] = local_pool.submit(original_enhancements' in source
    assert '"application_order_changed": False' in source
    assert '"relevant_input_fields_changed": False' in source
    assert "provider_start_policy_changed" in source


def test_weekly_source_prewarm_is_joined_before_committee_and_seeded_for_next_week() -> None:
    enrichment = (ROOT / "src/v182/reporting/weekly_enrichment_fast_v21_16.py").read_text(encoding="utf-8")
    full_bundle = (ROOT / "src/v182/reporting/weekly_full_bundle_v21_16.py").read_text(encoding="utf-8")
    assert "WEEKLY_SEED_PATH" in enrichment
    assert "max_prewarm=40" in enrichment
    assert "source_prewarm_future.result()" in enrichment
    assert '"joined_before_committee_gate": True' in enrichment
    assert "seed_path=WEEKLY_SEED_PATH" in full_bundle
    assert "max_persisted=40" in full_bundle


def test_friday_does_not_rescore_daily_tactical_after_weekly_committee() -> None:
    workflow = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    bundle = (ROOT / "src/v182/reporting/weekly_full_bundle_v21_16.py").read_text(encoding="utf-8")
    reuse = (ROOT / "src/v182/reporting/friday_tactical_reuse_v21_16.py").read_text(encoding="utf-8")
    assert "python -m v182.reporting.daily_tct_ct_runner" not in workflow
    assert "friday_tactical_reuse" in bundle
    assert '"rescoring_calls": 0' in reuse
    assert '"source_gate_calls": 0' in reuse
    assert '"entry_exit_recompute_calls": 0' in reuse
    assert '"network_calls": 0' in reuse
