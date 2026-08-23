from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import subprocess
import sys
import time

from v182.audit import identity_hydration

ROOT = Path(__file__).resolve().parents[3]
VERSION = "WEEKLY_TAIL_PARALLEL_V21_16_3_IDENTITY_IN_PROCESS"


def _run_module(root: Path, module: str) -> dict:
    started = time.perf_counter()
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-m", module],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "module": module,
        "returncode": int(result.returncode),
        "status": "SUCCESS" if result.returncode == 0 else "FAILED",
        "wall_seconds": round(time.perf_counter() - started, 6),
        "stdout_tail": (result.stdout or "")[-3000:],
        "stderr_tail": (result.stderr or "")[-3000:],
    }


def _run_identity_hydration(root: Path) -> dict:
    """Publish the diagnostic worklist in-process; it has no decision/state authority."""
    started = time.perf_counter()
    try:
        result = identity_hydration.run(root)
        return {
            "module": "v182.audit.identity_hydration",
            "returncode": 0,
            "status": "SUCCESS",
            "wall_seconds": round(time.perf_counter() - started, 6),
            "execution_mode": "IN_PROCESS_THREAD",
            "result": result,
        }
    except Exception as exc:
        return {
            "module": "v182.audit.identity_hydration",
            "returncode": 1,
            "status": "FAILED",
            "wall_seconds": round(time.perf_counter() - started, 6),
            "execution_mode": "IN_PROCESS_THREAD",
            "error": type(exc).__name__,
            "detail": str(exc)[:500],
        }


def _tct_lane(root: Path) -> dict:
    """Keep TCT state writers ordered while preserving historical fail-soft behavior."""
    started = time.perf_counter()
    tactical = _run_module(root, "v182.reporting.tactical_shadow_bundle_run")
    postmarket = _run_module(root, "v182.reporting.tct_postmarket_bundle_run")
    return {
        "status": "SUCCESS" if tactical["returncode"] == 0 and postmarket["returncode"] == 0 else "COMPLETED_WITH_NONBLOCKING_ERRORS",
        "wall_seconds": round(time.perf_counter() - started, 6),
        "steps": {"tactical_shadow": tactical, "postmarket": postmarket},
        "blocking": False,
        "state_order_preserved": True,
    }


def run(root: Path = ROOT) -> dict:
    started = time.perf_counter()
    audit_dir = root / "outputs" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Tactical/Postmarket share TCT state and remain serial in their own isolated
    # subprocess lane. Identity hydration is diagnostic only: the governed identity
    # overlay was already applied by WAVE01 itself, so its light worklist publisher
    # can run in-process without another Python interpreter startup.
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="weekly-tail") as pool:
        futures = {
            "tct_lane": pool.submit(_tct_lane, root),
            "decision_brief": pool.submit(_run_module, root, "v182.reporting.decision_brief_v21_16"),
            "etf_fund_flows": pool.submit(_run_module, root, "v182.reporting.etf_fund_flows_shadow_run"),
            "criteria_governance": pool.submit(_run_module, root, "v182.reporting.criteria_governance_audit"),
            "identity_hydration": pool.submit(_run_identity_hydration, root),
        }
        results = {name: future.result() for name, future in futures.items()}

    essential_failures = [
        name
        for name in ("decision_brief", "criteria_governance")
        if int(results[name].get("returncode", 1)) != 0
    ]
    nonblocking_failures = []
    tct = results["tct_lane"]
    for name, item in tct.get("steps", {}).items():
        if int(item.get("returncode", 1)) != 0:
            nonblocking_failures.append(name)
    for name in ("etf_fund_flows", "identity_hydration"):
        if int(results[name].get("returncode", 1)) != 0:
            nonblocking_failures.append(name)

    payload = {
        "status": "SUCCESS" if not essential_failures else "FAILED_ESSENTIAL_WEEKLY_TAIL",
        "version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "wall_seconds": round(time.perf_counter() - started, 6),
        "parallel_workers": 5,
        "parallel_lanes": [
            "TCT_TACTICAL_THEN_POSTMARKET_SERIAL_LANE",
            "DECISION_BRIEF",
            "ETF_FUND_FLOWS",
            "CRITERIA_GOVERNANCE",
            "IDENTITY_HYDRATION_DIAGNOSTIC",
        ],
        "identity_hydration_removed_from_pre_bundle_critical_path": True,
        "identity_overlay_application_still_owned_by_wave01": True,
        "identity_hydration_decision_influence": False,
        "identity_hydration_execution_mode": "IN_PROCESS_THREAD",
        "identity_hydration_interpreter_startup_avoided": True,
        "tct_state_writers_parallelized": False,
        "legacy_tail_modules_subprocess_isolated": True,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
        "essential_failures": essential_failures,
        "nonblocking_failures": nonblocking_failures,
        "results": results,
    }
    (audit_dir / "WEEKLY_TAIL_PARALLEL_V21_16.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    if essential_failures:
        raise RuntimeError("WEEKLY_TAIL_ESSENTIAL_FAILURE:" + ",".join(essential_failures))
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
