from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
import json

from v182.reporting import ci_entry_watch_v22_2_1
from v182.reporting import market_orientation_v22_2
from v182.reporting import weekly_unified_super_runner_v22_1 as core

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_1"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_1.json"


def run(root: Path = ROOT) -> dict:
    """V22.2.1: fast Weekly core + lightweight orientation + governed CI entry gate.

    WAVE09 remains disabled. Market orientation does not alter selection scoring or
    candidate decisions; it only gates entry review after the existing technical
    trigger. The three lightweight market sources run in one background branch while
    the independent Weekly core executes, so their wall-clock latency is normally
    hidden by the much longer core. Potential upside is explanatory only.
    """
    started = perf_counter()
    audit_dir = root / "outputs/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    market_payload: dict = {}
    watch_payload: dict = {}
    error = None
    market_join_wait_seconds = None
    market_overlap_started = False
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-orientation-overlap") as pool:
            market_future = pool.submit(market_orientation_v22_2.run, root=root)
            market_overlap_started = True
            payload = core.run(root=root)
            wait_started = perf_counter()
            market_payload = market_future.result()
            market_join_wait_seconds = round(float(perf_counter() - wait_started), 6)

        watch_payload = ci_entry_watch_v22_2_1.run(root=root)
        if watch_payload.get("status") != "SUCCESS":
            raise RuntimeError(f"V22_2_1_CI_WATCH_FAILED:{watch_payload.get('status')}")
        payload = dict(payload)
        payload["market_orientation_v22_2"] = market_payload
        payload["ci_entry_watch_v22_2_1"] = watch_payload
        return payload
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:700]}"
        raise
    finally:
        orientation = market_payload.get("orientation", {}) if isinstance(market_payload, dict) else {}
        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            "market_orientation_total_seconds": market_payload.get("total_seconds"),
            "market_orientation_overlaps_weekly_core": True,
            "market_orientation_overlap_started": market_overlap_started,
            "market_orientation_join_wait_seconds": market_join_wait_seconds,
            "market_orientation_us": orientation.get("us"),
            "market_orientation_europe": orientation.get("europe"),
            "market_orientation_global": orientation.get("global"),
            "ci_candidate_rows": watch_payload.get("candidate_rows"),
            "ci_ready_for_review": watch_payload.get("ready_for_review"),
            "ci_wait": watch_payload.get("wait"),
            "ci_market_blocks": watch_payload.get("market_blocks"),
            "ci_market_cautions": watch_payload.get("market_cautions"),
            "ci_potential_available": watch_payload.get("potential_available"),
            "wave09_disabled": True,
            "selection_score_changed": False,
            "selection_decision_changed": False,
            "criteria_weights_changed": False,
            "selection_thresholds_changed": False,
            "market_entry_gate_active": True,
            "potential_is_explanatory_only": True,
            "t1_t2_scope": "ACTION_TCT_ONLY",
            "real_orders_enabled": False,
        }
        (audit_dir / AUDIT_NAME).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(core.previous.previous.previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
