from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
import json

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

    prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-morningstar-prefetch")
    holder: dict[str, Future | None] = {"future": None}
    metrics = {
        "morningstar_prefetch_started": False,
        "morningstar_prefetch_wait_seconds": 0.0,
        "morningstar_prefetch_fallback": False,
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

    pipeline._collect_wave5_wave6_parallel = collect_56_with_morningstar_prefetch
    morningstar_actions.load_authorized_snapshot = morningstar_from_prefetch
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
        pipeline._export_excel_reports = original_exports
        prefetch_pool.shutdown(wait=True, cancel_futures=False)

        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            **metrics,
            "morningstar_application_position_changed": False,
            "morningstar_source_function_changed": False,
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
