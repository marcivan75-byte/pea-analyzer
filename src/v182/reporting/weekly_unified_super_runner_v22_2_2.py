from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json

from v182.reporting import ci_selection_gate_v22_2_2
from v182.reporting import weekly_unified_super_runner_v22_2_1 as previous

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_2"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_2.json"


def run(root: Path = ROOT) -> dict:
    """V22.2.2 = V22.2.1 plus explicit final CI selection gates and source links."""
    started = perf_counter()
    audit_dir = root / "outputs/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    gate_payload: dict = {}
    error = None
    try:
        payload = previous.run(root=root)
        gate_payload = ci_selection_gate_v22_2_2.run(root=root, ensure_upstream=False)
        if gate_payload.get("status") not in {"SUCCESS", "NO_CANDIDATES"}:
            raise RuntimeError(f"V22_2_2_SELECTION_GATE_FAILED:{gate_payload.get('status')}")
        payload = dict(payload)
        payload["ci_selection_gate_v22_2_2"] = gate_payload
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
            "selection_gate_status": gate_payload.get("status"),
            "selection_gate_input_candidates": gate_payload.get("input_candidates"),
            "selection_gate_selected": gate_payload.get("selected"),
            "selection_gate_rejected": gate_payload.get("rejected"),
            "selection_score_min": 77.0,
            "confidence_score_min": 66.0,
            "action_consensus_upside_min_pct": 20.0,
            "etf_consensus_gate": False,
            "boursorama_and_investing_links_in_final_selection": True,
            "base_scoring_formula_changed": False,
            "wave09_disabled": True,
            "t1_t2_scope": "ACTION_TCT_ONLY",
            "real_orders_enabled": False,
        }
        (audit_dir / AUDIT_NAME).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(previous.core.previous.previous.previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
