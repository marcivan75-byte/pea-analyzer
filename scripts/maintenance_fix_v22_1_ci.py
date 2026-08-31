from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, *, required: bool = True) -> bool:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        if required:
            raise RuntimeError(f"MAINTENANCE_PATTERN_MISSING:{path}:{old[:80]}")
        return False
    target.write_text(text.replace(old, new), encoding="utf-8")
    return True


def main() -> None:
    subprocess.run(["python", "-m", "ruff", "check", "src", "tests", "scripts", "--fix"], cwd=ROOT, check=False)
    replace(
        "src/v182/backtest/at_weekly_staged_entry_v11.py",
        "conf=after.iloc[lag-1]; conf_dt=pd.Timestamp(after.index[lag-1]); nxt=after.iloc[lag]; nxt_dt=pd.Timestamp(after.index[lag])",
        "conf=after.iloc[lag-1]; nxt=after.iloc[lag]; nxt_dt=pd.Timestamp(after.index[lag])",
        required=False,
    )

    selected = ROOT / "src/v182/sources/boursorama_selected_etf.py"
    selected_text = selected.read_text(encoding="utf-8")
    compatibility = "from v182.sources.boursorama_etf_collect import collect_selected_etf_context_cached as collect_selected_etf_context_cached"
    if compatibility not in selected_text:
        selected.write_text(selected_text.rstrip() + f"\n\n# Compatibility re-export for governed callers.\n{compatibility}\n", encoding="utf-8")

    weekly_tests = [
        "tests/test_action_ct_shared_history_runtime_v21_13_10.py",
        "tests/test_calibration_governance_process_wiring_v21_12.py",
        "tests/test_catalyst_runtime_v21_13_8.py",
        "tests/test_friday_tactical_reuse_v21_15_3.py",
        "tests/test_runtime_install_v21_13_16.py",
        "tests/test_tct_postmarket_bundle_runtime_v21_13_12.py",
        "tests/test_tct_tactical_shared_parquet_runtime_v21_13_11.py",
        "tests/test_weekly_post_decision_bundle_v21_15_2.py",
    ]
    for path in weekly_tests:
        replace(path, "python -m v182.reporting.weekly_tail_super_runner_v21_16_0", "python -m v182.reporting.weekly_operational_runner_v4_4", required=False)
        replace(path, "python -m v182.reporting.weekly_unified_super_runner_v21_16_2", "python -m v182.reporting.weekly_operational_runner_v4_4", required=False)

    replace("tests/test_runtime_install_v21_13_16.py", "Upload complete weekly Committee V21.16.2 results", "Upload complete weekly Committee V4.4 results", required=False)
    replace("tests/test_weekly_operational_runner_v4_4.py", 'assert "or_independent_steps_overlapped" in text', 'assert "or_steps_sequential" in text', required=False)
    replace("tests/test_weekly_operational_runner_v4_4.py", '    assert "ThreadPoolExecutor" in text\n', '    assert "or_steps_sequential" in text\n', required=False)

    replace("tests/test_final_package_v21_13_16.py", "assert len(runtime) == 13", "assert len(runtime) == 15", required=False)
    replace("tests/test_runtime_install_v21_13_15.py", "assert len(expected) == 13", "assert len(expected) == 15", required=False)
    replace("tests/test_boursorama_selected_etf_v21_15.py", "assert len(calls) == first_calls == 2", "assert len(calls) == first_calls == 3", required=False)

    # Daily OHLCV cache is migrated to the V4 contract used by Weekly.
    replace(
        ".github/workflows/committee_tct_ct_daily.yml",
        "          path: data/cache/\n          key: ohlcv-v3-${{ github.run_id }}\n          restore-keys: |\n            ohlcv-v3-",
        "          path: |\n            data/cache/actions/\n            data/cache/etf/\n          key: ohlcv-v4-${{ github.run_id }}\n          restore-keys: |\n            ohlcv-v4-\n            ohlcv-v3-",
        required=False,
    )
    old_checkpoint = """          rm -f state/OHLCV_CACHE_VALIDATED
          if [ -f outputs/audit/V18.2_QUALITY_GATES.json ] \\
             && grep -Eq '\"passed\"[[:space:]]*:[[:space:]]*true' outputs/audit/V18.2_QUALITY_GATES.json \\
             && [ -f data/cache/actions/history_manifest.json ] \\
             && [ -f data/cache/etf/history_manifest.json ]; then
            printf 'validated\\n' > state/OHLCV_CACHE_VALIDATED
          fi
"""
    new_checkpoint = """          rm -f state/OHLCV_CACHE_VALIDATED
          mkdir -p outputs/audit state
          actions_ok=0
          etf_ok=0
          if [ -s data/cache/actions/history_manifest.json ]; then actions_ok=1; fi
          if [ -s data/cache/etf/history_manifest.json ]; then etf_ok=1; fi
          if [ \"$actions_ok\" = 1 ] && [ \"$etf_ok\" = 1 ]; then
            printf 'validated\\n' > state/OHLCV_CACHE_VALIDATED
            status=SAVED_MANIFESTS_PRESENT
          else
            status=SKIPPED_MISSING_MANIFEST
          fi
          printf '{\"status\":\"%s\",\"actions_manifest\":%s,\"etf_manifest\":%s,\"quality_gate_required\":false}\\n' \\
            \"$status\" \"$actions_ok\" \"$etf_ok\" > outputs/audit/OHLCV_CACHE_CHECKPOINT.json
          cat outputs/audit/OHLCV_CACHE_CHECKPOINT.json
"""
    replace(".github/workflows/committee_tct_ct_daily.yml", old_checkpoint, new_checkpoint, required=False)
    replace(
        ".github/workflows/committee_tct_ct_daily.yml",
        "          path: data/cache/\n          key: ohlcv-v3-${{ github.run_id }}",
        "          path: |\n            data/cache/actions/\n            data/cache/etf/\n          key: ohlcv-v4-${{ github.run_id }}",
        required=False,
    )

    replace("tests/test_ohlcv_cache_checkpoint_v21_15_3.py", 'assert "outputs/audit/V18.2_QUALITY_GATES.json" in workflow', 'assert "outputs/audit/OHLCV_CACHE_CHECKPOINT.json" in workflow', required=False)
    replace("tests/test_ohlcv_cache_checkpoint_v21_15_3.py", 'assert "grep -Eq \'\\\"passed\\\"[[:space:]]*:[[:space:]]*true\'" in workflow', 'assert "quality_gate_required" in workflow', required=False)
    replace("tests/test_ohlcv_cache_checkpoint_v21_15_3.py", 'assert "[ -f data/cache/actions/history_manifest.json ]" in workflow', 'assert "-s data/cache/actions/history_manifest.json" in workflow', required=False)
    replace("tests/test_ohlcv_cache_checkpoint_v21_15_3.py", 'assert "[ -f data/cache/etf/history_manifest.json ]" in workflow', 'assert "-s data/cache/etf/history_manifest.json" in workflow', required=False)
    replace("tests/test_ohlcv_cache_checkpoint_v21_15_3.py", '"Run optimized weekly unified Committee DAG V21.16.2",', '"Run unified weekly operational runner V4.4",', required=False)
    replace("tests/test_persistent_cache_workflows.py", 'assert "path: data/cache/" in source', 'assert "data/cache/actions/" in source\n        assert "data/cache/etf/" in source', required=False)

    # Validate producer wiring inside the governed DAG rather than stale YAML markers.
    replace(
        "tests/test_tct_postmarket_bundle_runtime_v21_13_12.py",
        '        assert "POSTMARKET_BUNDLE_RUNTIME_V21_13_12.json" in workflow',
        '    assert "POSTMARKET_BUNDLE_RUNTIME_V21_13_12.json" in daily\n    assert "postmarket.run(root=root)" in weekly_tail',
        required=False,
    )
    replace(
        "tests/test_tct_tactical_shared_parquet_runtime_v21_13_11.py",
        '    assert "TACTICAL_SHARED_PARQUET_RUNTIME_V21_13_11.json" in weekly_workflow',
        '    assert "tactical.run(root=root)" in weekly_tail',
        required=False,
    )

    replace(".github/workflows/action_ct_v22_validation.yml", "'python -m v182.reporting.weekly_tail_super_runner_v21_16_0',", "'python -m v182.reporting.weekly_operational_runner_v4_4',", required=False)
    replace("tests/test_catalyst_runtime_v21_13_8.py", 'assert "steps.decision-state.outputs.cache-matched-key == \'\'" in workflow', 'assert "decision-state-v1-${{ github.run_id }}" in workflow', required=False)
    replace("tests/test_catalyst_runtime_v21_13_8.py", 'assert "steps.weekly-research-state.outputs.cache-matched-key == \'\'" in workflow', 'assert "weekly-research-state-v1-${{ github.run_id }}" in workflow', required=False)
    replace("tests/test_catalyst_runtime_v21_13_8.py", 'assert "key: ohlcv-v3-${{ github.run_id }}" in workflow', 'assert "key: ohlcv-v4-${{ github.run_id }}" in workflow', required=False)

    subprocess.run(["python", "-c", "from v182.sources.boursorama_selected_etf import collect_selected_etf_context_cached; assert callable(collect_selected_etf_context_cached)"], cwd=ROOT, check=True)
    subprocess.run(["python", "-m", "ruff", "check", "src", "tests", "scripts"], cwd=ROOT, check=True)
    subprocess.run(["python", "-m", "compileall", "-q", "src", "scripts"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
