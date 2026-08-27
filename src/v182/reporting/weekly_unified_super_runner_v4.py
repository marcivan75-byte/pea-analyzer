from __future__ import annotations

from pathlib import Path
from time import perf_counter
import json

from v182.reporting import ci_light_v4, ci_selection_gate_v4


ROOT = Path(__file__).resolve().parents[3]
AUDIT = Path("outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V4.json")
VERSION = "WEEKLY_UNIFIED_SUPER_RUNTIME_V4_2_INDEPENDENT_LIGHT_T1T2_V3"


def exit_code(payload: dict) -> int:
    return 0 if payload.get("status") == "SUCCESS" else 2


def run(
    root: Path = ROOT,
    *,
    ensure_upstream: bool = True,
    run_ci_light: bool = True,
    existing_ci_light: dict | None = None,
) -> dict:
    started = perf_counter()
    error: str | None = None
    selection: dict = {}
    light: dict = {}
    status = "FAILED_EXCEPTION"
    timings: dict[str, float] = {}
    try:
        phase = perf_counter()
        selection = ci_selection_gate_v4.run(root=root, ensure_upstream=ensure_upstream)
        timings["selection_seconds"] = round(perf_counter() - phase, 6)
        if run_ci_light:
            phase = perf_counter()
            light = ci_light_v4.run(root=root)
            timings["ci_light_seconds"] = round(perf_counter() - phase, 6)
        else:
            light = dict(existing_ci_light or {})
            timings["ci_light_seconds"] = 0.0
            if light.get("status") not in {"SUCCESS", "NO_UPSTREAM_ROWS"}:
                raise RuntimeError("MISSING_SUCCESSFUL_INDEPENDENT_CI_LIGHT_RESULT")
        status = "SUCCESS" if selection.get("status") == "SUCCESS" and light.get("status") == "SUCCESS" else "PARTIAL_FAILURE"
        return {"status": status, "version": VERSION, "selection": selection, "ci_light": light, "timings_seconds": timings}
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
                    "phase_timings_seconds": timings,
                    "source_collection_passes": 2 if run_ci_light else 1,
                    "upstream_recomputed": ensure_upstream,
                    "ci_light_executed_here": run_ci_light,
                    "ci_light_reused_from_completed_independent_core": not run_ci_light,
                    "ci_light_reuses_selection_context": False,
                    "ci_light_independent_process": True,
                    "selection_status": selection.get("status"),
                    "ci_light_status": light.get("status"),
                    "investing_enabled": False,
                    "tradingview_enabled": True,
                    "source_can_create_ci_light_candidate": True,
                    "source_can_create_ci_candidate": False,
                    "reference_score_source_influence": 0.0,
                    "etf_analyst_consensus_required": False,
                    "etf_boursorama_exact_fiche_required": True,
                    "etf_morningstar_missing_tv_medium_long_fallback": True,
                    "etf_morningstar_fallback_minimum_stars": 4,
                    "t1_t2_scope": "ACTION_TCT_ONLY",
                    "t1_t2_formula_version": "T1T2_V3_2026_08_STRICT_SEQUENCE",
                    "t1_t2_live_orders_enabled": False,
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
