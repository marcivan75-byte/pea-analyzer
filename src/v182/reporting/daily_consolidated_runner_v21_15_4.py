from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json
import traceback

from v182.reporting import daily_fast_collection_run as collection
from v182.reporting import daily_tactical_super_runner_v21_15_4 as tactical
from v182.reporting import etf_structure_state_replay as etf_replay
from v182.reporting import wave3_cpu_budget_v21_15_4 as wave3_cpu
from v182.reporting.earnings_clock_v21_15_4 import refresh_frame as refresh_earnings_clock


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_CONSOLIDATED_RUNTIME_V21_15_4"

_ORIGINAL_FAST_INSTALL = collection.DailyFastRuntime.install
_ORIGINAL_FAST_RESTORE = collection.DailyFastRuntime.restore


def _bootstrap_safe_fast_install(self) -> None:
    """Capture enriched masters even when retained fast state is not usable yet.

    The historical fast runtime returned immediately when ``enabled`` was false,
    so a fallback/full bootstrap run could never capture the enriched masters and
    therefore could never promote a valid state for the next run.  Keep all fast
    source substitutions disabled in that situation, but always wrap save_master
    so a successful full run can seed the retained state.
    """
    if self.enabled:
        return _ORIGINAL_FAST_INSTALL(self)

    def capture_save_master(frame, path):
        self.original_save_master(frame, path)
        name = Path(path).name
        if name == collection._ACTION_OUTPUT:
            self.captured["ACTION"] = frame.copy(deep=True)
        elif name == collection._ETF_OUTPUT:
            self.captured["ETF"] = frame.copy(deep=True)

    collection.legacy.save_master = capture_save_master


def _bootstrap_safe_fast_restore(self) -> None:
    if self.enabled:
        return _ORIGINAL_FAST_RESTORE(self)
    collection.legacy.save_master = self.original_save_master


def _safe_nonblocking(name: str, runner) -> tuple[dict, dict | None, float]:
    started = perf_counter()
    try:
        return runner(), None, perf_counter() - started
    except Exception as exc:
        return {}, {
            "step": name,
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": traceback.format_exc(limit=5),
        }, perf_counter() - started


def _run_collection_optimized_locals() -> tuple[dict, dict]:
    """Apply only equivalence-safe daily optimizations around collection."""
    original_loader = collection._load_fast_state
    original_wave3 = collection.waves.wave3_local_features
    original_prefetch = collection.topdown_prefetch.fetch_external
    original_fixed_window = collection._fixed_window_fetcher
    original_runtime_install = collection.DailyFastRuntime.install
    original_runtime_restore = collection.DailyFastRuntime.restore
    diagnostics: dict = {
        "earnings_clock": {
            "status": "NOT_APPLIED_NO_FAST_STATE",
            "network_calls": 0,
            "source_timestamp_changed": False,
        },
        "wave3_cpu_budget": wave3_cpu.audit_contract(),
        "fast_state_bootstrap": {
            "status": "ENABLED",
            "capture_enriched_masters_on_full_fallback": True,
            "decision_logic_changed": False,
            "source_contract_changed": False,
        },
        "topdown_early_prefetch": {
            "status": "DISABLED_TO_PRESERVE_WAVE09_CURRENT_WINDOW_FRESHNESS",
            "reason": "RUN_START_WINDOW_COULD_MISS_ARTICLES_PUBLISHED_BEFORE_WAVE09",
            "gdelt_request_set_changed": False,
            "gdelt_rate_limit_changed": False,
        },
    }

    def current_loader():
        actions, etf, manifest, mode = original_loader()
        if mode in {"DELTA_ONLY", "RECONCILE_CACHE"} and not actions.empty:
            actions, clock = refresh_earnings_clock(actions)
            diagnostics["earnings_clock"] = {**clock, "status": "APPLIED", "fast_mode": mode}
        return actions, etf, manifest, mode

    def disabled_prefetch(prepared, *, fred_api_key):
        # Return an intentional fingerprint mismatch immediately. The fast WAVE09
        # wrapper then uses its fail-closed historical `original_wave9` path.
        return collection.topdown_prefetch.ExternalTopdown(
            macro=None,
            news_results={},
            query_fingerprint="EARLY_PREFETCH_DISABLED_FOR_FRESHNESS",
        )

    collection._load_fast_state = current_loader
    collection.waves.wave3_local_features = wave3_cpu.wave3_local_features
    collection.topdown_prefetch.fetch_external = disabled_prefetch
    collection._fixed_window_fetcher = lambda _anchor, original_fetch: original_fetch
    collection.DailyFastRuntime.install = _bootstrap_safe_fast_install
    collection.DailyFastRuntime.restore = _bootstrap_safe_fast_restore
    try:
        return collection.run(), diagnostics
    finally:
        collection._load_fast_state = original_loader
        collection.waves.wave3_local_features = original_wave3
        collection.topdown_prefetch.fetch_external = original_prefetch
        collection._fixed_window_fetcher = original_fixed_window
        collection.DailyFastRuntime.install = original_runtime_install
        collection.DailyFastRuntime.restore = original_runtime_restore


def run(root: Path = ROOT) -> dict:
    """Daily production entrypoint with historical blocking semantics preserved.

    Collection remains blocking. ETF structural replay remains fail-soft as in the
    former workflow `continue-on-error` step. The tactical DAG owns its historical
    blocking core/enrichment and fail-soft SHADOW/postmarket branches internally.
    No model, source contract, score, weight or threshold is changed here.
    """
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    collection_started = perf_counter()
    collection_payload, local_optimizations = _run_collection_optimized_locals()
    collection_seconds = perf_counter() - collection_started

    replay_payload, replay_error, replay_seconds = _safe_nonblocking(
        "ETF_STRUCTURE_STATE_REPLAY",
        lambda: etf_replay.run(root=root),
    )

    tactical_started = perf_counter()
    tactical_payload = tactical.run(root=root)
    tactical_seconds = perf_counter() - tactical_started

    payload = {
        "status": "SUCCESS_DAILY_CONSOLIDATED" if replay_error is None else "SUCCESS_DAILY_CONSOLIDATED_WITH_ETF_REPLAY_WARNING",
        "version": VERSION,
        "single_python_process": True,
        "former_major_python_entrypoints": 5,
        "current_major_python_entrypoints": 1,
        "interpreter_boundaries_removed": 4,
        "blocking_semantics": {
            "collection": "BLOCKING",
            "etf_structure_replay": "FAIL_SOFT",
            "daily_tactical_core_and_selected_context": "BLOCKING",
            "tactical_shadow_and_postmarket": "FAIL_SOFT_RECORDED",
        },
        "local_optimizations": local_optimizations,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "t1_t2_scope_changed": False,
        "real_orders_enabled": False,
        "steps": {
            "collection": {
                "status": collection_payload.get("status"),
                "fast_mode": collection_payload.get("daily_fast_collection", {}).get("mode"),
                "fast_state_promoted": collection_payload.get("daily_fast_collection", {}).get("promoted"),
            },
            "etf_structure_replay": {
                "status": replay_payload.get("status"),
                "error": replay_error,
            },
            "tactical_dag": {
                "status": tactical_payload.get("status"),
                "version": tactical_payload.get("version"),
            },
        },
        "timings_seconds": {
            "collection": round(float(collection_seconds), 6),
            "etf_structure_replay": round(float(replay_seconds), 6),
            "tactical_dag": round(float(tactical_seconds), 6),
            "total": round(float(perf_counter() - started), 6),
        },
    }
    (auditdir / "DAILY_CONSOLIDATED_RUNTIME_V21_15_4.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
