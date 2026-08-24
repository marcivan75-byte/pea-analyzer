from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
import json

from v182.audit import quality as quality_module
from v182.reporting import run as pipeline
from v182.reporting import weekly_unified_super_runner_v22 as previous
from v182.sources import morningstar_actions


ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_1"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_1.json"


def _parallel_excel_exports(actions, etfs, before, after, quality_checks, run_profile):
    """Write the three independent final workbooks concurrently.

    The exact historical export functions, titles and output paths are preserved.
    Inputs are read-only and each worker owns a different output file.
    """
    if run_profile == "DAILY_TACTICAL":
        return False

    from v182.reporting.exports import export_master_excel, export_run_report

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="weekly-final-excel") as pool:
        futures = [
            pool.submit(
                export_master_excel,
                actions,
                pipeline.OUTPUTS / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx",
                "V21.3 Actions PEA actualisées — 1829",
            ),
            pool.submit(
                export_master_excel,
                etfs,
                pipeline.OUTPUTS / "V18.2_PEA_ETF_ACTUALISE.xlsx",
                "V21.3 ETF PEA actualisés",
            ),
            pool.submit(
                export_run_report,
                before,
                after,
                quality_checks,
                pipeline.OUTPUTS / "V18.2_RUN_REPORT.xlsx",
            ),
        ]
        for future in futures:
            future.result()
    return True


def run(root: Path = ROOT) -> dict:
    """V22.1 scheduling-only gains on top of the validated V22 baseline.

    Changes are deliberately narrow:
    - pre-read the authorized Morningstar Action snapshot while WAVE05/WAVE06 run;
      its observations are still applied only at the historical WAVE06B location;
    - overlap the final read-only WAVE99 inventory audit with quality-gate checks;
      quality does not return until the final audit has also completed successfully;
    - generate the three independent final Excel workbooks concurrently.

    No same-provider network concurrency is added. No criterion, weight, threshold,
    universe, PIT rule, missing-data policy, provider freshness rule or order policy
    is changed.
    """
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    original_collect_56 = pipeline._collect_wave5_wave6_parallel
    original_morningstar = morningstar_actions.load_authorized_snapshot
    original_exports = pipeline._export_excel_reports
    original_audit = pipeline._audit
    original_quality = quality_module.run_quality_gates

    prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-morningstar-prefetch")
    final_audit_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-final-audit")
    holder: dict[str, Future | None] = {"future": None}
    final_audit: dict[str, Future | None] = {"future": None}
    metrics = {
        "morningstar_prefetch_started": False,
        "morningstar_prefetch_wait_seconds": 0.0,
        "morningstar_prefetch_fallback": False,
        "final_audit_quality_overlap_started": False,
        "final_audit_quality_join_wait_seconds": 0.0,
        "parallel_excel_exports": True,
    }

    def collect_56_with_morningstar_prefetch(
        actions_df,
        etf_with_tickers,
        cfg,
        finnhub_key,
        *,
        run_wave5,
        run_wave6,
    ):
        if holder["future"] is None:
            ms_cfg = cfg.get("morningstar_actions", {})
            snapshot = root / ms_cfg.get(
                "snapshot_path", "inputs/V21_ACTION_MORNINGSTAR_RATINGS.csv"
            )
            worklist = root / ms_cfg.get(
                "worklist_path", "outputs/gaps/V21_ACTION_MORNINGSTAR_WORKLIST.csv"
            )
            holder["future"] = prefetch_pool.submit(
                original_morningstar,
                actions_df.copy(),
                snapshot,
                worklist,
            )
            metrics["morningstar_prefetch_started"] = True
        return original_collect_56(
            actions_df,
            etf_with_tickers,
            cfg,
            finnhub_key,
            run_wave5=run_wave5,
            run_wave6=run_wave6,
        )

    def morningstar_from_prefetch(actions_df, snapshot_path, worklist_path):
        future = holder.get("future")
        if future is None:
            metrics["morningstar_prefetch_fallback"] = True
            return original_morningstar(actions_df, snapshot_path, worklist_path)
        wait_started = perf_counter()
        try:
            return future.result()
        except Exception:
            metrics["morningstar_prefetch_fallback"] = True
            return original_morningstar(actions_df, snapshot_path, worklist_path)
        finally:
            metrics["morningstar_prefetch_wait_seconds"] = round(
                float(perf_counter() - wait_started), 6
            )

    def audit_with_final_overlap(
        actions,
        etfs,
        wave_id,
        *,
        failures=None,
        source_context="",
    ):
        if wave_id != "WAVE_99_FINAL":
            return original_audit(
                actions,
                etfs,
                wave_id,
                failures=failures,
                source_context=source_context,
            )
        if final_audit["future"] is not None:
            return final_audit["future"].result()
        final_audit["future"] = final_audit_pool.submit(
            original_audit,
            actions,
            etfs,
            wave_id,
            failures=failures,
            source_context=source_context,
        )
        metrics["final_audit_quality_overlap_started"] = True
        return None

    def quality_joining_final_audit(*args, **kwargs):
        result = original_quality(*args, **kwargs)
        future = final_audit.get("future")
        if future is not None:
            wait_started = perf_counter()
            future.result()
            metrics["final_audit_quality_join_wait_seconds"] = round(
                float(perf_counter() - wait_started), 6
            )
        return result

    pipeline._collect_wave5_wave6_parallel = collect_56_with_morningstar_prefetch
    morningstar_actions.load_authorized_snapshot = morningstar_from_prefetch
    pipeline._audit = audit_with_final_overlap
    quality_module.run_quality_gates = quality_joining_final_audit
    pipeline._export_excel_reports = _parallel_excel_exports

    payload: dict = {}
    error: str | None = None
    try:
        payload = previous.run(root=root)
        return payload
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:700]}"
        raise
    finally:
        pipeline._collect_wave5_wave6_parallel = original_collect_56
        morningstar_actions.load_authorized_snapshot = original_morningstar
        pipeline._audit = original_audit
        quality_module.run_quality_gates = original_quality
        pipeline._export_excel_reports = original_exports
        prefetch_pool.shutdown(wait=True, cancel_futures=False)
        final_audit_pool.shutdown(wait=True, cancel_futures=False)

        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            **metrics,
            "morningstar_application_position_changed": False,
            "morningstar_source_function_changed": False,
            "final_audit_function_changed": False,
            "quality_gate_function_changed": False,
            "quality_waits_for_final_audit": True,
            "same_provider_network_concurrency_added": False,
            "excel_export_functions_changed": False,
            "excel_output_paths_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "universe_changed": False,
            "pit_logic_changed": False,
            "missing_data_policy_changed": False,
            "provider_freshness_policy_changed": False,
            "t1_t2_scope_changed": False,
            "real_orders_enabled": False,
        }
        (auditdir / AUDIT_NAME).write_text(
            json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(previous.previous.previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
