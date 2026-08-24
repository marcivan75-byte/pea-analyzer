from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json

from v182.reporting import ci_light_v4, ci_selection_gate_v4


ROOT = Path(__file__).resolve().parents[3]
AUDIT = Path("outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V4.json")
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V4_1"


def exit_code(payload: dict) -> int:
    return 0 if payload.get("status") == "SUCCESS" else 2


def run(root: Path = ROOT) -> dict:
    started = perf_counter()
    error: str | None = None
    selection: dict = {}
    light: dict = {}
    status = "FAILED_EXCEPTION"
    try:
        selection = ci_selection_gate_v4.run(root=root, ensure_upstream=True)
        if selection.get("status") != "SUCCESS":
            status = "BLOCKED_SELECTION_GATE"
            return {"status": status, "selection": selection}
        light = ci_light_v4.run(root=root)
        if light.get("status") != "SUCCESS":
            status = "BLOCKED_CI_LIGHT"
            return {"status": status, "selection": selection, "ci_light": light}
        status = "SUCCESS"
        return {"status": status, "version": VERSION, "selection": selection, "ci_light": light}
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:700]}"
        raise
    finally:
        target = root / AUDIT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "version": VERSION,
                    "status": status,
                    "error": error,
                    "total_seconds": round(perf_counter() - started, 6),
                    "selection_status": selection.get("status"),
                    "ci_light_status": light.get("status"),
                    "investing_enabled": False,
                    "tradingview_enabled": True,
                    "source_can_create_candidate": False,
                    "reference_score_source_influence": 0.0,
                    "etf_analyst_consensus_required": False,
                    "real_orders_enabled": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    result = run(ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code(result))
