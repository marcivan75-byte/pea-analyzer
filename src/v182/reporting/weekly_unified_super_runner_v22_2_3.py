from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json

from v182.reporting import ci_light_v22_2_3
from v182.reporting import weekly_unified_super_runner_v22_2_2 as previous

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_3"
AUDIT_NAME = "WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_3.json"


def run(root: Path = ROOT) -> dict:
    """V22.2.3 keeps the full weighted CI and adds a strict source-confirmed LIGHT view."""
    started = perf_counter()
    audit_dir = root / "outputs/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    light_payload: dict = {}
    error = None
    try:
        payload = previous.run(root=root)
        light_payload = ci_light_v22_2_3.run(root=root)
        if light_payload.get("status") not in {"SUCCESS", "NO_UPSTREAM_ROWS"}:
            raise RuntimeError(f"CI_LIGHT_V22_2_3_FAILED:{light_payload.get('status')}")
        payload = dict(payload)
        payload["ci_light_v22_2_3"] = light_payload
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
            "full_ci_preserved": True,
            "full_ci_all_weighted_criteria_preserved": True,
            "ci_light_status": light_payload.get("status"),
            "ci_light_selected": light_payload.get("selected"),
            "ci_light_selected_by_horizon": light_payload.get("selected_by_horizon"),
            "ci_light_boursorama_positive_required": True,
            "ci_light_investing_buy_or_strong_buy_required": True,
            "ci_light_horizon_mapping": {"TCT": "DAILY", "CT": "WEEKLY", "MT": "MONTHLY"},
            "etf_requires_explicit_boursorama_analyst_recommendation": True,
            "source_can_create_candidate": False,
            "selection_score_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "wave09_disabled": True,
            "t1_t2_scope": "ACTION_TCT_ONLY",
            "real_orders_enabled": False,
        }
        (audit_dir / AUDIT_NAME).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    payload = run(ROOT)
    raise SystemExit(previous.previous.core.previous.previous.previous.base._exit_code(payload))


if __name__ == "__main__":
    main()
