from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock
from time import perf_counter
import json

from v182.audit import quality as quality_module
from v182.decision import committee_master as committee_decision
from v182.reporting import run as pipeline
from v182.reporting import weekly_unified_super_runner_v22 as previous
from v182.risk import beta_correlation_engine as risk_engine
from v182.sources import etf_inception_data, etf_structural_data, morningstar_actions


ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_1"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_1.json"


def _parallel_excel_exports(actions, etfs, before, after, quality_checks, run_profile):
    """Write the three independent final workbooks concurrently."""
    if run_profile == "DAILY_TACTICAL":
        return False
    from v182.reporting.exports import export_master_excel, export_run_report

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="weekly-final-excel") as pool:
        futures = [
            pool.submit(export_master_excel, actions, pipeline.OUTPUTS / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx", "V21.3 Actions PEA actualisées — 1829"),
            pool.submit(export_master_excel, etfs, pipeline.OUTPUTS / "V18.2_PEA_ETF_ACTUALISE.xlsx", "V21.3 ETF PEA actualisés"),
            pool.submit(export_run_report, before, after, quality_checks, pipeline.OUTPUTS / "V18.2_RUN_REPORT.xlsx"),
        ]
        for future in futures:
            future.result()
    return True


def run(root: Path = ROOT) -> dict:
    """V22.1 scheduling/cache-only gains on top of the validated V22 baseline.

    Exact repeated work is reused only within the current run: ETF same-URL source
    responses/PDF text, Committee percentile ranks, and Risk returns/beta metrics.
    Morningstar is prefetched but applied at WAVE06B, final audit overlaps quality,
    and disjoint final Excel exports run concurrently. Decision semantics are intact.
    """
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    original_collect_56 = pipeline._collect_wave5_wave6_parallel
    original_morningstar = morningstar_actions.load_authorized_snapshot
    original_exports = pipeline._export_excel_reports
    original_audit = pipeline._audit
    original_quality = quality_module.run_quality_gates
    original_struct_get = etf_structural_data._get
    original_inception_get = etf_inception_data._get
    original_struct_pdf = etf_structural_data._pdf_text
    original_inception_pdf = etf_inception_data._pdf_text
    original_pct_score = committee_decision._pct_score
    original_to_returns = risk_engine.to_returns
    original_compute_beta = risk_engine.compute_beta_metrics

    prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-morningstar-prefetch")
    final_audit_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-final-audit")
    holder: dict[str, Future | None] = {"future": None}
    final_audit: dict[str, Future | None] = {"future": None}
    cache_lock = Lock()
    pct_lock = Lock()
    risk_lock = Lock()
    http_cache: dict[str, object] = {}
    pdf_text_cache: dict[str, str] = {}
    pct_cache: dict[tuple[int, str], object] = {}
    pct_inflight: dict[tuple[int, str], Event] = {}
    pct_series_refs: dict[int, object] = {}
    returns_cache: dict[int, object] = {}
    returns_refs: dict[int, object] = {}
    beta_cache: dict[tuple[int, int], dict] = {}
    beta_refs: dict[int, object] = {}
    metrics = {
        "morningstar_prefetch_started": False,
        "morningstar_prefetch_wait_seconds": 0.0,
        "morningstar_prefetch_fallback": False,
        "final_audit_quality_overlap_started": False,
        "final_audit_quality_join_wait_seconds": 0.0,
        "parallel_excel_exports": True,
        "etf_exact_url_http_cache_hits": 0,
        "etf_exact_url_http_cache_misses": 0,
        "etf_amundi_exact_url_reuse_hits": 0,
        "etf_pdf_text_cache_hits": 0,
        "etf_pdf_text_cache_misses": 0,
        "committee_percentile_cache_hits": 0,
        "committee_percentile_cache_misses": 0,
        "committee_percentile_cache_waits": 0,
        "risk_returns_cache_hits": 0,
        "risk_returns_cache_misses": 0,
        "risk_beta_cache_hits": 0,
        "risk_beta_cache_misses": 0,
    }

    def cached_get(original_get):
        def wrapped(session, url: str, *, timeout: int = 25):
            with cache_lock:
                cached = http_cache.get(url)
                if cached is not None:
                    metrics["etf_exact_url_http_cache_hits"] += 1
                    if "amundietf.fr/pdfDocuments/monthly-factsheet/" in url:
                        metrics["etf_amundi_exact_url_reuse_hits"] += 1
                    return cached
                metrics["etf_exact_url_http_cache_misses"] += 1
            response = original_get(session, url, timeout=timeout)
            with cache_lock:
                http_cache[url] = response
            return response
        return wrapped

    def cached_pdf_text(original_pdf):
        def wrapped(content: bytes) -> str:
            digest = sha256(content).hexdigest()
            with cache_lock:
                cached = pdf_text_cache.get(digest)
                if cached is not None:
                    metrics["etf_pdf_text_cache_hits"] += 1
                    return cached
                metrics["etf_pdf_text_cache_misses"] += 1
            text = original_pdf(content)
            with cache_lock:
                pdf_text_cache[digest] = text
            return text
        return wrapped

    def cached_pct_score(series, direction: str):
        key = (id(series), str(direction))
        while True:
            owner = False
            with pct_lock:
                pct_series_refs[id(series)] = series
                cached = pct_cache.get(key)
                if cached is not None:
                    metrics["committee_percentile_cache_hits"] += 1
                    return cached
                event = pct_inflight.get(key)
                if event is None:
                    event = Event()
                    pct_inflight[key] = event
                    metrics["committee_percentile_cache_misses"] += 1
                    owner = True
                else:
                    metrics["committee_percentile_cache_waits"] += 1
            if not owner:
                event.wait()
                continue
            try:
                result = original_pct_score(series, direction)
            except BaseException:
                with pct_lock:
                    pct_inflight.pop(key, None)
                    event.set()
                raise
            with pct_lock:
                pct_cache[key] = result
                pct_inflight.pop(key, None)
                event.set()
            return result

    def cached_to_returns(prices):
        key = id(prices)
        with risk_lock:
            returns_refs[key] = prices
            cached = returns_cache.get(key)
            if cached is not None:
                metrics["risk_returns_cache_hits"] += 1
                return cached
            metrics["risk_returns_cache_misses"] += 1
        result = original_to_returns(prices)
        with risk_lock:
            returns_cache[key] = result
        return result

    def cached_compute_beta(returns, benchmark):
        key = (id(returns), id(benchmark))
        with risk_lock:
            beta_refs[id(returns)] = returns
            beta_refs[id(benchmark)] = benchmark
            cached = beta_cache.get(key)
            if cached is not None:
                metrics["risk_beta_cache_hits"] += 1
                return dict(cached)
            metrics["risk_beta_cache_misses"] += 1
        result = original_compute_beta(returns, benchmark)
        with risk_lock:
            beta_cache[key] = dict(result)
        return result

    def collect_56_with_morningstar_prefetch(actions_df, etf_with_tickers, cfg, finnhub_key, *, run_wave5, run_wave6):
        if holder["future"] is None:
            ms_cfg = cfg.get("morningstar_actions", {})
            snapshot = root / ms_cfg.get("snapshot_path", "inputs/V21_ACTION_MORNINGSTAR_RATINGS.csv")
            worklist = root / ms_cfg.get("worklist_path", "outputs/gaps/V21_ACTION_MORNINGSTAR_WORKLIST.csv")
            holder["future"] = prefetch_pool.submit(original_morningstar, actions_df.copy(), snapshot, worklist)
            metrics["morningstar_prefetch_started"] = True
        return original_collect_56(actions_df, etf_with_tickers, cfg, finnhub_key, run_wave5=run_wave5, run_wave6=run_wave6)

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
            metrics["morningstar_prefetch_wait_seconds"] = round(float(perf_counter() - wait_started), 6)

    def audit_with_final_overlap(actions, etfs, wave_id, *, failures=None, source_context=""):
        if wave_id != "WAVE_99_FINAL":
            return original_audit(actions, etfs, wave_id, failures=failures, source_context=source_context)
        if final_audit["future"] is not None:
            return final_audit["future"].result()
        final_audit["future"] = final_audit_pool.submit(original_audit, actions, etfs, wave_id, failures=failures, source_context=source_context)
        metrics["final_audit_quality_overlap_started"] = True
        return None

    def quality_joining_final_audit(*args, **kwargs):
        result = original_quality(*args, **kwargs)
        future = final_audit.get("future")
        if future is not None:
            wait_started = perf_counter()
            future.result()
            metrics["final_audit_quality_join_wait_seconds"] = round(float(perf_counter() - wait_started), 6)
        return result

    pipeline._collect_wave5_wave6_parallel = collect_56_with_morningstar_prefetch
    morningstar_actions.load_authorized_snapshot = morningstar_from_prefetch
    pipeline._audit = audit_with_final_overlap
    quality_module.run_quality_gates = quality_joining_final_audit
    pipeline._export_excel_reports = _parallel_excel_exports
    etf_structural_data._get = cached_get(original_struct_get)
    etf_inception_data._get = cached_get(original_inception_get)
    etf_structural_data._pdf_text = cached_pdf_text(original_struct_pdf)
    etf_inception_data._pdf_text = cached_pdf_text(original_inception_pdf)
    committee_decision._pct_score = cached_pct_score
    risk_engine.to_returns = cached_to_returns
    risk_engine.compute_beta_metrics = cached_compute_beta

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
        etf_structural_data._get = original_struct_get
        etf_inception_data._get = original_inception_get
        etf_structural_data._pdf_text = original_struct_pdf
        etf_inception_data._pdf_text = original_inception_pdf
        committee_decision._pct_score = original_pct_score
        risk_engine.to_returns = original_to_returns
        risk_engine.compute_beta_metrics = original_compute_beta
        prefetch_pool.shutdown(wait=True, cancel_futures=False)
        final_audit_pool.shutdown(wait=True, cancel_futures=False)

        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            **metrics,
            "risk_cache_scope": "RUN_LOCAL_EXACT_PRICE_SERIES_AND_BENCHMARK_IDENTITY",
            "risk_formulas_changed": False,
            "committee_percentile_cache_scope": "RUN_LOCAL_EXACT_SERIES_ID_PLUS_DIRECTION",
            "committee_percentile_semantics_changed": False,
            "etf_structure_collectors_remain_sequential": True,
            "etf_exact_url_reuse_only": True,
            "etf_failed_http_requests_cached": False,
            "etf_source_urls_changed": False,
            "etf_parser_rules_changed": False,
            "etf_evidence_levels_changed": False,
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
        (auditdir / AUDIT_NAME).write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(previous.previous.previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
