from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json

from v182.reporting import ci_entry_confidence_v22_2
from v182.reporting import weekly_unified_super_runner_v22_1 as previous

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2.json"


def run(root: Path = ROOT) -> dict:
    """V22.2 = V22.1 runtime gains + shortlisted CI entry/confidence watch.

    The V22.2 layer is deliberately downstream-only: it consumes final Committee
    decisions and cached OHLCV for selected candidates. It never changes the
    selection score, selection decision, criteria, weights, thresholds or orders.
    """
    started = perf_counter()
    audit_dir = root / "outputs/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    entry_payload: dict = {}
    error = None
    try:
        payload = previous.run(root=root)
        entry_payload = ci_entry_confidence_v22_2.run(root=root)
        if entry_payload.get("status") != "SUCCESS":
            raise RuntimeError(f"V22_2_ENTRY_CONFIDENCE_FAILED:{entry_payload.get('status')}")
        payload = dict(payload)
        payload["ci_entry_confidence_v22_2"] = entry_payload
        return payload
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:700]}"
        raise
    finally:
        audit = {
            "version": VERSION,
            "status": payload.get("status") if payload else "FAILED_EXCEPTION",
            "error": error,
            "total_seconds": round(float(perf_counter() - started), 6),
            "ci_entry_confidence_status": entry_payload.get("status"),
            "ci_entry_candidate_rows": entry_payload.get("candidate_rows"),
            "ci_entry_ready_for_review": entry_payload.get("ready_for_review"),
            "ci_entry_strong_confidence": entry_payload.get("strong_confidence"),
            "wave09_disabled": True,
            "selection_score_changed": False,
            "selection_decision_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "universe_changed": False,
            "t1_t2_scope_changed": False,
            "real_orders_enabled": False,
        }
        (audit_dir / AUDIT_NAME).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(previous.previous.previous.previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
