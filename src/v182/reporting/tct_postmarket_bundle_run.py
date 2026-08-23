from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Callable
import json
import traceback

from v182.reporting import tct_next_session_catalyst_run_v24_4_2 as catalyst
from v182.reporting import tct_pit_ohlc_ledger_v24_4_2 as ohlc_ledger
from v182.reporting import tct_v24_4_2_pit_lineage as lineage
from v182.reporting import tct_v24_4_2_pit_validator as validator


ROOT = Path(__file__).resolve().parents[3]
VERSION = "V21.15.6_TCT_POSTMARKET_SINGLE_PROCESS_RUNTIME"


def _run_step(name: str, runner: Callable[[], dict]) -> tuple[dict, dict | None, float]:
    started = perf_counter()
    try:
        payload = runner()
        return payload, None, perf_counter() - started
    except Exception as exc:
        return {}, {
            "step": name,
            "type": type(exc).__name__,
            "message": str(exc)[:500],
            "traceback": traceback.format_exc(limit=5),
        }, perf_counter() - started


def _run_lineage_dtype_safe(root: Path) -> dict:
    """Run governed PIT lineage while allowing boolean labels in legacy float columns.

    Some persisted catalyst ledgers infer ``pit_label_evaluable`` as float because
    all historical values are empty/NaN. Pandas then rejects False/True assignment.
    Converting that one storage column to object before the governed lineage call
    changes no fingerprint, label rule, threshold or outcome; it only makes the
    already-defined boolean domain assignable.
    """
    original_apply = lineage.apply_lineage

    def compatible_apply(catalyst_ledger, ohlc_ledger_frame, **kwargs):
        ledger = catalyst_ledger.copy()
        if "pit_label_evaluable" in ledger.columns:
            ledger["pit_label_evaluable"] = ledger["pit_label_evaluable"].astype(object)
        return original_apply(ledger, ohlc_ledger_frame, **kwargs)

    lineage.apply_lineage = compatible_apply
    try:
        return lineage.run(root=root)
    finally:
        lineage.apply_lineage = original_apply


def run(root: Path = ROOT) -> dict:
    """Run the existing POSTMARKET V24.4.2 chain in one Python process.

    The four governed modules remain the owners of their data, PIT logic,
    fingerprints, scoring and outputs. Every step is attempted even when a prior
    step fails, preserving historical continue-on-error semantics.
    """
    started = perf_counter()
    auditdir = root / "outputs" / "audit"
    auditdir.mkdir(parents=True, exist_ok=True)

    # Governance dependency: PIT lineage must be materialized before Catalyst
    # consumes the postmarket state. Keep Validator last. This is the historical
    # governed order and changes no scoring/threshold/fingerprint semantics.
    specifications: list[tuple[str, Callable[[], dict]]] = [
        ("PIT_OHLC_V24.4.2", lambda: ohlc_ledger.run(root=root)),
        ("PIT_LINEAGE_V24.4.2", lambda: _run_lineage_dtype_safe(root)),
        ("POSTMARKET_CATALYST_V24.4.2", lambda: catalyst.run(root=root, phase="POSTMARKET")),
        ("PIT_VALIDATOR_V24.4.2", lambda: validator.run(root=root)),
    ]

    step_payloads: dict[str, dict] = {}
    step_runtime: dict[str, float] = {}
    errors: list[dict] = []
    for name, runner in specifications:
        payload, error, seconds = _run_step(name, runner)
        step_payloads[name] = payload
        step_runtime[name] = round(float(seconds), 6)
        if error is not None:
            errors.append(error)

    result = {
        "status": "SUCCESS_POSTMARKET_SINGLE_PROCESS" if not errors else "POSTMARKET_SINGLE_PROCESS_WITH_STEP_ERRORS",
        "version": VERSION,
        "step_order": [name for name, _ in specifications],
        "all_steps_attempted": True,
        "postmarket_phase_explicit": True,
        "previous_python_processes": 4,
        "current_python_processes": 1,
        "interpreter_startups_avoided": 3,
        "pit_label_storage_dtype_fix": True,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "candidate_scope_changed": False,
        "news_query_policy_changed": False,
        "pit_logic_changed": False,
        "fingerprint_logic_changed": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
        "step_runtime_seconds": step_runtime,
        "steps": {
            name: {
                "status": payload.get("status"),
                "version": payload.get("version"),
            }
            for name, payload in step_payloads.items()
        },
        "errors": errors,
        "total_seconds": round(float(perf_counter() - started), 6),
    }
    audit_path = auditdir / "POSTMARKET_BUNDLE_RUNTIME_V21_13_12.json"
    audit_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if errors:
        raise RuntimeError(
            "POSTMARKET V21.15.6 completed with step error(s): "
            + ", ".join(str(error.get("step")) for error in errors)
        )
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
