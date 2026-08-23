from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json
import traceback

from v182.reporting import daily_fast_collection_run as collection
from v182.reporting import daily_tactical_super_runner_v21_15_4 as tactical
from v182.reporting import etf_structure_state_replay as etf_replay
from v182.reporting.earnings_clock_v21_15_4 import refresh_frame as refresh_earnings_clock


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_CONSOLIDATED_RUNTIME_V21_15_4"


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


def _run_collection_with_current_earnings_clock() -> tuple[dict, dict]:
    """Refresh relative earnings fields only when a validated fast state is loaded."""
    original_loader = collection._load_fast_state
    diagnostics: dict = {
        "status": "NOT_APPLIED_NO_FAST_STATE",
        "network_calls": 0,
        "source_timestamp_changed": False,
    }

    def current_loader():
        nonlocal diagnostics
        actions, etf, manifest, mode = original_loader()
        if mode in {"DELTA_ONLY", "RECONCILE_CACHE"} and not actions.empty:
            actions, diagnostics = refresh_earnings_clock(actions)
            diagnostics = {**diagnostics, "status": "APPLIED", "fast_mode": mode}
        return actions, etf, manifest, mode

    collection._load_fast_state = current_loader
    try:
        return collection.run(), diagnostics
    finally:
        collection._load_fast_state = original_loader


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
    collection_payload, earnings_clock = _run_collection_with_current_earnings_clock()
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
        "earnings_clock": earnings_clock,
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
