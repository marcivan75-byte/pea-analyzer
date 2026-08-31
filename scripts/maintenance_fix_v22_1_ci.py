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
    # 1) Remove the safe Ruff debt that currently blocks several unrelated CI gates.
    subprocess.run(
        ["python", "-m", "ruff", "check", "src", "tests", "scripts", "--fix"],
        cwd=ROOT,
        check=False,
    )
    # Ruff deliberately classifies this one unused assignment as an unsafe fix.
    replace(
        "src/v182/backtest/at_weekly_staged_entry_v11.py",
        "conf=after.iloc[lag-1]; conf_dt=pd.Timestamp(after.index[lag-1]); nxt=after.iloc[lag]; nxt_dt=pd.Timestamp(after.index[lag])",
        "conf=after.iloc[lag-1]; nxt=after.iloc[lag]; nxt_dt=pd.Timestamp(after.index[lag])",
    )

    # 2) V4.4 became the governed weekly entry point. Legacy tests must validate the
    # current façade instead of requiring removed direct workflow invocations.
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
        replace(
            path,
            "python -m v182.reporting.weekly_tail_super_runner_v21_16_0",
            "python -m v182.reporting.weekly_operational_runner_v4_4",
            required=False,
        )
        replace(
            path,
            "python -m v182.reporting.weekly_unified_super_runner_v21_16_2",
            "python -m v182.reporting.weekly_operational_runner_v4_4",
            required=False,
        )

    replace(
        "tests/test_runtime_install_v21_13_16.py",
        "Upload complete weekly Committee V21.16.2 results",
        "Upload complete weekly Committee V4.4 results",
    )
    replace(
        "tests/test_weekly_operational_runner_v4_4.py",
        'assert "or_independent_steps_overlapped" in text',
        'assert "or_steps_sequential" in text',
    )

    # 3) The governed IC+Lasso selector added scipy/scikit-learn to the canonical
    # runtime contract. Project and requirements already agree; only cardinality
    # assertions were stale.
    replace("tests/test_final_package_v21_13_16.py", "assert len(runtime) == 13", "assert len(runtime) == 15")
    replace("tests/test_runtime_install_v21_13_15.py", "assert len(expected) == 13", "assert len(expected) == 15")

    # 4) Boursorama ETF now has an explicit palmares fallback when MS/SRI fields
    # are incomplete. The first uncached collection therefore makes three page
    # requests, while the second call must still make none.
    replace(
        "tests/test_boursorama_selected_etf_v21_15.py",
        "assert len(calls) == first_calls == 2",
        "assert len(calls) == first_calls == 3",
    )

    # 5) Update OHLCV checkpoint tests to the V4 fail-closed manifest contract.
    replace(
        "tests/test_ohlcv_cache_checkpoint_v21_15_3.py",
        'assert "outputs/audit/V18.2_QUALITY_GATES.json" in workflow',
        'assert "outputs/audit/OHLCV_CACHE_CHECKPOINT.json" in workflow',
    )
    replace(
        "tests/test_ohlcv_cache_checkpoint_v21_15_3.py",
        'assert "grep -Eq \'\\\"passed\\\"[[:space:]]*:[[:space:]]*true\'" in workflow',
        'assert "quality_gate_required" in workflow',
    )
    replace(
        "tests/test_ohlcv_cache_checkpoint_v21_15_3.py",
        'assert "[ -f data/cache/actions/history_manifest.json ]" in workflow',
        'assert "-s data/cache/actions/history_manifest.json" in workflow',
    )
    replace(
        "tests/test_ohlcv_cache_checkpoint_v21_15_3.py",
        'assert "[ -f data/cache/etf/history_manifest.json ]" in workflow',
        'assert "-s data/cache/etf/history_manifest.json" in workflow',
    )
    replace(
        "tests/test_ohlcv_cache_checkpoint_v21_15_3.py",
        '"Run optimized weekly unified Committee DAG V21.16.2",',
        '"Run unified weekly operational runner V4.4",',
    )

    # 6) V4 caches Actions and ETF subtrees explicitly rather than the whole cache
    # directory. Preserve the shared namespace requirement without enforcing the
    # obsolete single-path spelling.
    replace(
        "tests/test_persistent_cache_workflows.py",
        'assert "path: data/cache/" in source',
        'assert "data/cache/actions/" in source\n        assert "data/cache/etf/" in source',
    )

    # 7) The CT validation workflow must inspect the governed V4.4 weekly façade.
    replace(
        ".github/workflows/action_ct_v22_validation.yml",
        "'python -m v182.reporting.weekly_tail_super_runner_v21_16_0',",
        "'python -m v182.reporting.weekly_operational_runner_v4_4',",
    )

    # 8) Legacy state-cache tests expected removed cache-miss branching. In V4.4
    # the caches are always restored and the current IDs/keys themselves are the
    # contract to validate.
    replace(
        "tests/test_catalyst_runtime_v21_13_8.py",
        'assert "steps.decision-state.outputs.cache-matched-key == \'\'" in workflow',
        'assert "decision-state-v1-${{ github.run_id }}" in workflow',
    )
    replace(
        "tests/test_catalyst_runtime_v21_13_8.py",
        'assert "steps.weekly-research-state.outputs.cache-matched-key == \'\'" in workflow',
        'assert "weekly-research-state-v1-${{ github.run_id }}" in workflow',
    )

    # Final source-level gates. Do not auto-ignore residual lint or test failures.
    subprocess.run(["python", "-m", "ruff", "check", "src", "tests", "scripts"], cwd=ROOT, check=True)
    subprocess.run(["python", "-m", "compileall", "-q", "src", "scripts"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
