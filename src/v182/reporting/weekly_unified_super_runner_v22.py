from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json

from v182.reporting import waves
from v182.reporting import weekly_unified_super_runner_v21_16_2 as previous


ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22.json"


def run(root: Path = ROOT) -> dict:
    """V22 baseline: V21.16.2 unchanged except WAVE09 is intentionally disabled.

    WAVE09 is currently too expensive for the Weekly runtime budget. V22 keeps its
    call contract intact but replaces the live TOPDOWN collection with an explicit
    no-op for this run. No WAVE09 observation is created, no stale WAVE09 value is
    injected, and downstream modules continue under their existing missing-data
    rules. The criteria registries, weights and thresholds are not modified.
    """
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    original_wave9 = waves.wave9_topdown
    state = {
        "calls": 0,
        "actions_rows_seen": 0,
        "etf_rows_seen": 0,
    }

    def wave9_disabled(actions_df, etf_df, cfg, fred_api_key=None):
        state["calls"] += 1
        state["actions_rows_seen"] = int(len(actions_df))
        state["etf_rows_seen"] = int(len(etf_df))
        diagnostics = {
            "version": "V22_WAVE09_DISABLED",
            "status": "DISABLED_BY_V22_RUNTIME_BASELINE",
            "reason": "WAVE09_TOPDOWN_DEFERRED_FOR_PROGRESSIVE_REINTRODUCTION",
            "actions_observations": 0,
            "etf_observations": 0,
            "external_calls": 0,
            "fred_calls": 0,
            "gdelt_calls": 0,
            "real_orders_enabled": False,
        }
        return [], [], diagnostics

    waves.wave9_topdown = wave9_disabled
    payload: dict = {}
    error: str | None = None
    try:
        payload = previous.run(root=root)
        return payload
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:700]}"
        raise
    finally:
        waves.wave9_topdown = original_wave9
        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            "wave09_disabled": True,
            "wave09_live_collection_executed": False,
            "wave09_external_calls": 0,
            "wave09_calls_intercepted": int(state["calls"]),
            "wave09_actions_rows_seen": int(state["actions_rows_seen"]),
            "wave09_etf_rows_seen": int(state["etf_rows_seen"]),
            "wave09_reintroduction_policy": "PROGRESSIVE_SUBFUNCTIONS_AFTER_V22_BASELINE",
            "criteria_registry_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "pit_logic_changed_outside_wave09": False,
            "missing_data_policy_changed": False,
            "decision_surface_changed_by_user_authorized_wave09_removal": True,
            "real_orders_enabled": False,
        }
        (auditdir / AUDIT_NAME).write_text(
            json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(previous.previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
