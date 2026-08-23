from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from v182.reporting.daily_context_baseline import (
    load_context_baseline,
    publish_context_baseline,
    publish_from_outputs,
)
from v182.reporting.daily_tct_ct_runner import _daily_exact_scope


def _canonical_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    actions = pd.DataFrame(
        {
            "isin": [f"FR{i:010d}" for i in range(1829)],
            "name": [f"Action {i}" for i in range(1829)],
            "last_close": [100.0] * 1829,
            "slow_consensus": [4.0] * 1829,
        }
    )
    etfs = pd.DataFrame(
        {
            "isin": [f"LU{i:010d}" for i in range(102)],
            "name": [f"ETF {i}" for i in range(102)],
            "last_close": [10.0] * 102,
            "slow_structure": ["UCITS"] * 102,
        }
    )
    return actions, etfs


def test_daily_baseline_write_never_advances_last_full_refresh_timestamp(tmp_path: Path):
    actions, etfs = _canonical_frames()
    friday = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
    monday = friday + timedelta(days=3)
    first = publish_context_baseline(
        actions, etfs, tmp_path, full_refresh=True, profile="WEEKLY", now=friday
    )
    actions.loc[0, "last_close"] = 101.0
    second = publish_context_baseline(
        actions, etfs, tmp_path, full_refresh=False, profile="DAILY", now=monday
    )
    assert first["last_full_refresh_utc"] == second["last_full_refresh_utc"]
    assert second["last_snapshot_utc"] != second["last_full_refresh_utc"]
    loaded_actions, _, meta = load_context_baseline(tmp_path, now=monday)
    assert loaded_actions.loc[0, "last_close"] == 101.0
    assert meta["slow_source_freshness_extended_by_daily_write"] is False


def test_daily_baseline_stale_after_governed_max_age(tmp_path: Path):
    actions, etfs = _canonical_frames()
    friday = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)
    publish_context_baseline(actions, etfs, tmp_path, full_refresh=True, profile="WEEKLY", now=friday)
    with pytest.raises(RuntimeError, match="DAILY_FAST_BASELINE_STALE"):
        load_context_baseline(
            tmp_path,
            max_full_age_days=8,
            now=friday + timedelta(days=8, minutes=1),
        )


def test_daily_baseline_rejects_noncanonical_universe(tmp_path: Path):
    actions, etfs = _canonical_frames()
    with pytest.raises(RuntimeError, match="DAILY_FAST_ACTION_CANONICAL_COUNT_REQUIRED"):
        publish_context_baseline(
            actions.iloc[:-1], etfs, tmp_path, full_refresh=True, profile="WEEKLY"
        )


def test_weekly_publication_requires_passed_full_quality_gate(tmp_path: Path):
    actions, etfs = _canonical_frames()
    outputs = tmp_path / "outputs"
    audit = outputs / "audit"
    audit.mkdir(parents=True)
    actions.to_csv(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", sep=";", index=False, encoding="utf-8-sig")
    etfs.to_csv(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv", sep=";", index=False, encoding="utf-8-sig")
    (audit / "V18.2_QUALITY_GATES.json").write_text(
        json.dumps({"passed": False, "expected_rows": {"ACTION": 1829, "ETF": 102}, "checks": []}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="FULL_QUALITY_GATE_NOT_PASSED"):
        publish_from_outputs(tmp_path)

    (audit / "V18.2_QUALITY_GATES.json").write_text(
        json.dumps({"passed": True, "expected_rows": {"ACTION": 1829, "ETF": 102}, "checks": [{"passed": True}]}),
        encoding="utf-8",
    )
    result = publish_from_outputs(tmp_path)
    assert result["full_quality_gate_passed"] is True
    assert result["actions_rows"] == 1829
    assert result["etf_rows"] == 102


def test_consolidated_runtime_cache_carries_all_cross_day_state():
    restore = Path(".github/actions/runtime-cache-restore/action.yml").read_text(encoding="utf-8")
    save = Path(".github/actions/runtime-cache-save/action.yml").read_text(encoding="utf-8")
    for text in (restore, save):
        assert "runtime-state-v21-16-${{ github.run_id }}" in text
        assert "data/cache/" in text
        assert "state/TCT_V24_1_7_T1_STATE.json" in text
        assert "state/tct_context/" in text
        assert "state/action_ct/" in text
        assert "state/action_ct_v22_1/" in text
        assert "state/provenance/" in text
        assert "state/sector_rotation_v2/" in text
        assert "state/etf_fund_flows/" in text
    assert "ohlcv-v3-" in restore
    assert "decision-state-v1-" in restore
    assert "weekly-research-state-v1-" in restore


def test_daily_workflow_uses_single_process_single_cache_fast_path():
    workflow = Path(".github/workflows/committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    assert "python -m v182.reporting.daily_fast_bundle_v21_16" in workflow
    assert "uses: ./.github/actions/runtime-cache-restore" in workflow
    assert "uses: ./.github/actions/runtime-cache-save" in workflow
    assert "key: ohlcv-v3-" not in workflow
    assert "key: decision-state-v1-" not in workflow
    assert "python -m v182.reporting.daily_fast_collection\n" not in workflow
    assert "python -m v182.reporting.daily_tct_ct_runner" not in workflow
    assert "python -m v182.reporting.tct_postmarket_bundle_run" not in workflow
    assert "python -m v182.reporting.run\n" not in workflow
    assert "python -m v182.reporting.tactical_shadow_bundle_run" not in workflow
    assert "python -m v182.reporting.etf_structure_state_replay" not in workflow
    assert 'PEA_YF_INCREMENTAL_PERIOD: "5d"' in workflow
    assert 'PEA_DAILY_BASELINE_MAX_AGE_DAYS: "8"' in workflow
    assert "runtime_job_summary_v21_16 --profile DAILY_TACTICAL" in workflow
    assert "continue-on-error: true" in workflow


def test_daily_bundle_preserves_order_and_nonblocking_postmarket():
    source = Path("src/v182/reporting/daily_fast_bundle_v21_16.py").read_text(encoding="utf-8")
    assert 'steps["fast_collection"]' in source
    assert 'steps["tct_ct"]' in source
    assert 'steps["postmarket"]' in source
    assert source.index('steps["fast_collection"]') < source.index('steps["tct_ct"]') < source.index('steps["postmarket"]')
    assert 'lambda: tct_postmarket_bundle_run.run(root), blocking=False' in source
    assert '"previous_python_processes": 3' in source
    assert '"current_python_processes": 1' in source
    assert '"interpreter_startups_avoided": 2' in source
    assert '_budget_result("DAILY_TACTICAL", wall_seconds)' in source


def test_daily_fast_source_contains_no_slow_network_wave_calls():
    source = Path("src/v182/reporting/daily_fast_collection.py").read_text(encoding="utf-8")
    forbidden = (
        "wave4_info_actions(",
        "wave5_consensus_finnhub(",
        "wave6_etf_info(",
        "wave9_topdown(",
        "wave_public_table(",
    )
    for token in forbidden:
        assert token not in source
    assert "wave_history(actions" in source
    assert "wave3_local_features(" in source
    assert "build_rotation_observations(actions)" in source


def test_daily_postmarket_defers_pit_audit_but_not_operational_context():
    source = Path("src/v182/reporting/tct_postmarket_bundle_run.py").read_text(encoding="utf-8")
    assert 'daily_fast = os.environ.get("PEA_RUN_PROFILE", "").strip().upper() == "DAILY_TACTICAL"' in source
    assert '("PIT_OHLC_V24.4.2"' in source
    assert '("POSTMARKET_CATALYST_V24.4.2"' in source
    assert 'deferred = ["PIT_LINEAGE_V24.4.2", "PIT_VALIDATOR_V24.4.2"]' in source
    assert '"daily_deferred_steps_have_decision_influence": False' in source


def test_daily_exact_t1_t2_scope_is_governed_top20_only():
    frame = pd.DataFrame(
        {
            "isin": [f"FR{i:010d}" for i in range(30)],
            "tct_baseline_rank": list(range(1, 31)),
            "tct_baseline_coverage": [0.80] * 18 + [0.59, 0.75] + [0.80] * 10,
        }
    )
    cfg = {"scope": {"baseline_top_n": 20, "baseline_min_coverage": 0.60}}
    scoped = _daily_exact_scope(frame, cfg)
    assert len(scoped) == 19
    assert scoped["tct_baseline_rank"].max() <= 20
    assert scoped["tct_baseline_coverage"].min() >= 0.60


def test_weekly_committee_keeps_exhaustive_tct_exact_research():
    source = Path("src/v182/reporting/committee_master_run.py").read_text(encoding="utf-8")
    assert "build_exact_timing_snapshot(actions_with_tct" in source
    assert "_daily_exact_scope" not in source


def test_weekly_workflow_uses_one_parent_one_cache_and_no_duplicate_subcommands():
    workflow = Path(".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    assert "python -m v182.reporting.weekly_full_bundle_v21_16" in workflow
    assert "uses: ./.github/actions/runtime-cache-restore" in workflow
    assert "uses: ./.github/actions/runtime-cache-save" in workflow
    assert "key: ohlcv-v3-" not in workflow
    assert "key: decision-state-v1-" not in workflow
    assert "key: weekly-research-state-v1-" not in workflow
    assert "python -m v182.reporting.weekly_unified_fast_v21_16" not in workflow
    assert "python -m v182.reporting.friday_tactical_reuse_v21_16" not in workflow
    assert "python -m v182.reporting.weekly_tail_parallel_v21_16" not in workflow
    assert "python -m v182.reporting.daily_tct_ct_runner" not in workflow
    assert "python -m v182.reporting.etf_structure_state_replay" not in workflow
    assert "python -m v182.reporting.tactical_shadow_bundle_run" not in workflow
    assert "python -m v182.reporting.tct_postmarket_bundle_run" not in workflow
    assert "python -m v182.reporting.decision_brief_v21_16" not in workflow
    assert "python -m v182.reporting.etf_fund_flows_shadow_run" not in workflow
    assert "python -m v182.reporting.criteria_governance_audit" not in workflow
    assert "python -m v182.audit.identity_hydration" not in workflow
    assert "runtime_job_summary_v21_16 --profile WEEKLY_FULL_COMMITTEE" in workflow
    assert "continue-on-error: true" in workflow


def test_weekly_full_bundle_preserves_parent_order_and_duration_budget():
    source = Path("src/v182/reporting/weekly_full_bundle_v21_16.py").read_text(encoding="utf-8")
    assert source.index('steps["unified"]') < source.index('steps["friday_tactical_reuse"]') < source.index('steps["weekly_tail"]')
    assert '"previous_parent_python_processes": 3' in source
    assert '"current_parent_python_processes": 1' in source
    assert '"parent_interpreter_startups_avoided": 2' in source
    assert '_budget_result("WEEKLY_FULL_COMMITTEE", wall_seconds)' in source


def test_weekly_tail_parallelism_preserves_tct_writer_order():
    source = Path("src/v182/reporting/weekly_tail_parallel_v21_16.py").read_text(encoding="utf-8")
    tactical_pos = source.index('"v182.reporting.tactical_shadow_bundle_run"')
    postmarket_pos = source.index('"v182.reporting.tct_postmarket_bundle_run"')
    assert tactical_pos < postmarket_pos
    assert "ThreadPoolExecutor(max_workers=5" in source
    assert '"decision_brief": pool.submit' in source
    assert '"etf_fund_flows": pool.submit' in source
    assert '"criteria_governance": pool.submit' in source
    assert '"identity_hydration": pool.submit(_run_identity_hydration, root)' in source
    assert '"identity_hydration_removed_from_pre_bundle_critical_path": True' in source
    assert '"identity_overlay_application_still_owned_by_wave01": True' in source
    assert '"identity_hydration_execution_mode": "IN_PROCESS_THREAD"' in source
    assert '"identity_hydration_interpreter_startup_avoided": True' in source
    assert '"tct_state_writers_parallelized": False' in source
    assert '"legacy_tail_modules_subprocess_isolated": True' in source


def test_weekly_unified_parallelism_is_dependency_safe():
    source = Path("src/v182/reporting/unified_runner.py").read_text(encoding="utf-8")
    assert "ThreadPoolExecutor(max_workers=3, thread_name_prefix=\"weekly-independent\")" in source
    assert 'pool.submit(_safe_step, "etf_structure"' in source
    assert "pool.submit(_cached_etf_mt, root)" in source
    assert 'pool.submit(_safe_step, "sector_rotation_v2"' in source
    assert "ThreadPoolExecutor(max_workers=workers, thread_name_prefix=\"post-committee\")" in source
    assert 'pool.submit(_safe_step, "risk_context"' in source
    assert 'pool.submit(_safe_step, "ci_explainability"' in source
    assert '"critical_path_parallelism_changed": True' in source


def test_weekly_lean_enrichment_drops_only_unused_xlsx_serialization():
    source = Path("src/v182/reporting/weekly_enrichment_fast_v21_16.py").read_text(encoding="utf-8")
    assert 'write_excel=wave_id == "WAVE_99_FINAL"' in source
    assert "V18.2_PEA_ACTIONS_ACTUALISE.xlsx" in source
    assert "V18.2_PEA_ETF_ACTUALISE.xlsx" in source
    assert "V18.2_RUN_REPORT.xlsx" in source
    assert '"network_collection_changed": False' in source
    assert '"quality_gates_changed": False' in source
    assert '"committee_ci_outputs_affected": False' in source


def test_duration_contract_is_static_and_has_authoritative_job_measurement():
    contract = json.loads(Path("config/RUNTIME_DURATION_CONTRACT_V21_16.json").read_text(encoding="utf-8"))
    assert contract["status"] == "STATIC_ARCHITECTURE_VALIDATED_MEASUREMENT_PENDING_USER_AUTHORIZED_RUN"
    budget = contract["v21_16_3_static_design_budget"]
    assert budget["targets_are_not_observed_runtime"] is True
    assert budget["daily_billable_budget_minutes"] == 6
    assert budget["weekly_billable_budget_minutes"] == 19
    assert contract["weekly_architecture"]["identity_hydration_strategy"]["moved_to_nonblocking_weekly_tail"] is True
    assert "IDENTITY_HYDRATION_DIAGNOSTIC" in contract["weekly_architecture"]["weekly_tail_parallel_lanes"]
    measurement = contract["measurement_contract"]
    assert measurement["core_bundle_runtime_is_authoritative"] is False
    assert measurement["authoritative_runtime_source"] == "outputs/audit/GITHUB_JOB_RUNTIME_V21_16.json_AND_GITHUB_STEP_SUMMARY"
    assert measurement["end_to_end_json_is_generated_after_artifact_upload"] is True
    invariants = contract["non_regression_invariants"]
    assert invariants["actions_universe_count"] == 1829
    assert invariants["action_criteria_count"] == 633
    assert invariants["etf_criteria_count"] == 268
    assert invariants["no_model_weight_change_for_runtime_optimization"] is True
    assert invariants["no_threshold_change_for_runtime_optimization"] is True
    assert invariants["no_universe_reduction_for_runtime_optimization"] is True


def test_runtime_optimization_never_reduces_canonical_universe_or_criteria_registry():
    integrity = json.loads(Path("config/FULL_REFERENTIAL_INTEGRITY.json").read_text(encoding="utf-8"))
    actions = json.loads(Path("config/V21_ACTIONS_CRITERIA_REGISTRY.json").read_text(encoding="utf-8"))
    etfs = json.loads(Path("config/V20_7_1_ETF_CRITERIA_REGISTRY.json").read_text(encoding="utf-8"))
    assert integrity["actions"]["universe_count"] == 1829
    assert actions["criteria_count"] == 633
    assert etfs["criteria_count"] == 268
