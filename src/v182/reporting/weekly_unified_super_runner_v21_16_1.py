from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from time import perf_counter
import json
import logging

import pandas as pd

from v182.decision import committee_master as committee_decision
from v182.reporting import committee_master_run
from v182.reporting import committee_master_v21_4
from v182.reporting import unified_runner as base


ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V21_16_1"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V21_16_1.json"
COMMITTEE_WORKERS = 2
logger = logging.getLogger(__name__)


def _parallel_safe_horizons(frame, registry, asset_class, horizons):
    """Exact historical horizon functions, scheduled two at a time.

    Each branch only reads the immutable current master/registry and returns its
    own DataFrames. Results are reassembled in the exact input horizon order.
    """
    ordered = list(horizons)

    def one(index_horizon):
        index, horizon = index_horizon
        try:
            decisions = committee_master_run.decisions_from_scores(
                frame, registry, asset_class, [horizon]
            )
            coverage = committee_master_run.criterion_coverage_report(
                frame, registry, asset_class, [horizon]
            )
            return index, decisions, coverage, None
        except Exception as exc:
            logger.exception("Committee %s %s failed without aborting other horizons", asset_class, horizon)
            failed = committee_master_run._failed_horizon(
                asset_class, horizon, registry.get("version", ""), exc
            )
            error = {
                "asset_class": asset_class,
                "horizon": horizon,
                "error": type(exc).__name__,
                "detail": str(exc)[:240],
            }
            return index, failed, pd.DataFrame(), error

    with ThreadPoolExecutor(
        max_workers=min(COMMITTEE_WORKERS, max(1, len(ordered))),
        thread_name_prefix=f"committee-{str(asset_class).lower()}-horizon",
    ) as pool:
        rows = list(pool.map(one, enumerate(ordered)))
    rows.sort(key=lambda item: item[0])
    decisions = [item[1] for item in rows]
    coverages = [item[2] for item in rows if item[2] is not None and not item[2].empty]
    failures = [item[3] for item in rows if item[3] is not None]
    return decisions, coverages, failures


def _parallel_decisions_from_scores(original):
    """Parallelize only multi-horizon read-only calls; one-horizon calls stay exact."""

    def wrapped(frame, registry, asset_class, horizons):
        ordered = list(horizons)
        if len(ordered) <= 1:
            return original(frame, registry, asset_class, ordered)
        with ThreadPoolExecutor(
            max_workers=min(COMMITTEE_WORKERS, len(ordered)),
            thread_name_prefix=f"reference-{str(asset_class).lower()}-horizon",
        ) as pool:
            futures = [
                pool.submit(original, frame, registry, asset_class, [horizon])
                for horizon in ordered
            ]
            parts = [future.result() for future in futures]
        return pd.concat(parts, ignore_index=True, sort=False) if parts else pd.DataFrame()

    return wrapped


def _memoized_resolver(original):
    """Deduplicate immutable criterion resolution inside one Weekly run.

    The cache is scoped to the lifetime of ``run`` and keyed by the exact
    DataFrame object plus canonical criterion name. A strong reference to every
    seen frame prevents CPython object-id reuse while the cache is active.
    Concurrent requests for the same key share one in-flight resolution; calls
    for different criteria remain concurrent.
    """
    cache = {}
    inflight = {}
    frame_refs = {}
    lock = Lock()
    stats = {"hits": 0, "misses": 0, "waits": 0, "entries": 0, "frames": 0}

    def wrapped(frame, name):
        key = (id(frame), str(name))
        while True:
            owner = False
            with lock:
                frame_refs[id(frame)] = frame
                stats["frames"] = len(frame_refs)
                if key in cache:
                    stats["hits"] += 1
                    return cache[key]
                event = inflight.get(key)
                if event is None:
                    event = Event()
                    inflight[key] = event
                    stats["misses"] += 1
                    owner = True
                else:
                    stats["waits"] += 1

            if not owner:
                event.wait()
                continue

            try:
                result = original(frame, name)
            except BaseException:
                with lock:
                    inflight.pop(key, None)
                    event.set()
                raise

            with lock:
                cache[key] = result
                stats["entries"] = len(cache)
                inflight.pop(key, None)
                event.set()
            return result

    return wrapped, stats


def run(root: Path = ROOT) -> dict:
    """Weekly unified runtime with proven-safe scheduling optimizations only.

    Optimizations:
    1. Committee horizon computations use the unchanged scoring/coverage functions
       with a two-worker read-only scheduler and deterministic horizon reassembly.
    2. V21.0 reference multi-horizon scoring uses the unchanged scoring function
       two horizons at a time.
    3. Sector Rotation V2 starts only after ETF structural refresh returns, and may
       overlap the remaining independent ETF MT branch. Committee still waits for
       Rotation V2 before reading its diagnostic status, preserving that dependency.
    4. Canonical criterion resolution is memoized only for the current Weekly run,
       so score/coverage and concurrent horizons reuse identical immutable Series.
    """
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    original_safe_horizons = committee_master_run._safe_horizons
    original_reference_decisions = committee_master_v21_4.decisions_from_scores
    original_resolve_field = committee_decision.resolve_field
    original_refresh = base.enrichment_run.run
    original_structure = base.etf_structure_refresh.run
    original_sector = base.sector_rotation_v2_shadow_run.run
    cached_resolve_field, resolver_cache_stats = _memoized_resolver(original_resolve_field)

    refresh_ok = {"value": False}
    sector_future = {"value": None}
    sector_lock = Lock()
    sector_metrics = {
        "started_after_etf_structure": False,
        "background_started": False,
        "actual_runtime_seconds": None,
    }

    def refresh_wrapped(*args, **kwargs):
        result = original_refresh(*args, **kwargs)
        refresh_ok["value"] = True
        return result

    def run_sector_measured(root_arg):
        sector_started = perf_counter()
        try:
            return original_sector(root_arg)
        finally:
            sector_metrics["actual_runtime_seconds"] = round(
                float(perf_counter() - sector_started), 6
            )

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-sector-overlap")

    def structure_wrapped(root_arg):
        result = original_structure(root_arg)
        if refresh_ok["value"]:
            with sector_lock:
                if sector_future["value"] is None:
                    sector_metrics["started_after_etf_structure"] = True
                    sector_metrics["background_started"] = True
                    sector_future["value"] = executor.submit(run_sector_measured, root_arg)
        return result

    def sector_wrapped(root_arg):
        with sector_lock:
            future = sector_future["value"]
        if future is None:
            return run_sector_measured(root_arg)
        return future.result()

    committee_master_run._safe_horizons = _parallel_safe_horizons
    committee_master_v21_4.decisions_from_scores = _parallel_decisions_from_scores(
        original_reference_decisions
    )
    committee_decision.resolve_field = cached_resolve_field
    base.enrichment_run.run = refresh_wrapped
    base.etf_structure_refresh.run = structure_wrapped
    base.sector_rotation_v2_shadow_run.run = sector_wrapped

    payload: dict = {}
    error: str | None = None
    try:
        payload = base.run(root=root)
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:700]}"
        raise
    finally:
        committee_master_run._safe_horizons = original_safe_horizons
        committee_master_v21_4.decisions_from_scores = original_reference_decisions
        committee_decision.resolve_field = original_resolve_field
        base.enrichment_run.run = original_refresh
        base.etf_structure_refresh.run = original_structure
        base.sector_rotation_v2_shadow_run.run = original_sector
        executor.shutdown(wait=True, cancel_futures=False)

        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            "committee_horizon_workers": COMMITTEE_WORKERS,
            "committee_horizon_output_order_preserved": True,
            "committee_scoring_functions_changed": False,
            "reference_scoring_functions_changed": False,
            "criterion_resolution_function_wrapped": True,
            "criterion_resolution_semantics_changed": False,
            "criterion_resolution_cache_scope": "RUN_LOCAL_FRAME_ID_PLUS_FIELD",
            "criterion_resolution_cache": dict(resolver_cache_stats),
            "sector_rotation_dependency_on_etf_structure_preserved": True,
            "committee_waits_for_sector_rotation": True,
            "sector_rotation_overlaps_remaining_etf_mt_when_possible": True,
            "sector_overlap": sector_metrics,
            "provider_freshness_policy_changed": False,
            "external_provider_concurrency_added_by_sector_overlap": False,
            "decision_logic_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "pit_logic_changed": False,
            "holdout_opened": False,
            "real_orders_enabled": False,
        }
        (auditdir / AUDIT_NAME).write_text(
            json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    return payload


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(base._exit_code(payload))


if __name__ == "__main__":
    main()
