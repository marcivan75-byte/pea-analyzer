from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import time

import pandas as pd

from v182.reporting import criteria_governance_audit
from v182.reporting import etf_fund_flows_shadow_run


ROOT = Path(__file__).resolve().parents[3]
AUDIT_NAME = "WEEKLY_POST_DECISION_BUNDLE_RUNTIME_V21_15_2.json"
WEEKLY_SNAPSHOT_DIR = Path("state/provenance/weekly_master_snapshot_v1")
WEEKLY_ACTIONS = WEEKLY_SNAPSHOT_DIR / "actions.parquet"
WEEKLY_ETF = WEEKLY_SNAPSHOT_DIR / "etf.parquet"
WEEKLY_MANIFEST = WEEKLY_SNAPSHOT_DIR / "manifest.json"


def _measured(name: str, func, root: Path) -> dict:
    started = time.perf_counter()
    try:
        result = func(root)
        return {
            "step": name,
            "status": "SUCCESS",
            "runtime_seconds": round(time.perf_counter() - started, 3),
            "result_status": result.get("status") if isinstance(result, dict) else None,
            "error": None,
        }
    except Exception as exc:
        return {
            "step": name,
            "status": "FAILED",
            "runtime_seconds": round(time.perf_counter() - started, 3),
            "result_status": None,
            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
        }


def _write_audit(root: Path, payload: dict) -> None:
    outdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / AUDIT_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _persist_weekly_master_snapshot(root: Path) -> dict:
    """Persist Friday enriched masters inside the already-saved provenance state.

    Daily can then reuse the latest weekly W09/top-down values without performing
    any W09 network call. The snapshot is a transport/cache artifact only; Daily
    dynamic waves still refresh their governed fields normally.
    """
    actions_path = root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    etf_path = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not actions_path.exists() or not etf_path.exists():
        raise RuntimeError("WEEKLY_ENRICHED_MASTERS_MISSING")
    actions = pd.read_csv(actions_path, sep=";", encoding="utf-8-sig", low_memory=False)
    etf = pd.read_csv(etf_path, sep=";", encoding="utf-8-sig", low_memory=False)
    if actions.empty or etf.empty or "isin" not in actions.columns or "isin" not in etf.columns:
        raise RuntimeError("WEEKLY_ENRICHED_MASTERS_INVALID")

    outdir = root / WEEKLY_SNAPSHOT_DIR
    outdir.mkdir(parents=True, exist_ok=True)
    actions_tmp = root / WEEKLY_ACTIONS.with_name(".actions.parquet.tmp")
    etf_tmp = root / WEEKLY_ETF.with_name(".etf.parquet.tmp")
    actions.to_parquet(actions_tmp, index=False)
    etf.to_parquet(etf_tmp, index=False)
    actions_tmp.replace(root / WEEKLY_ACTIONS)
    etf_tmp.replace(root / WEEKLY_ETF)
    payload = {
        "version": "WEEKLY_MASTER_SNAPSHOT_V1",
        "validated": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "actions_rows": int(len(actions)),
        "etf_rows": int(len(etf)),
        "actions_unique_isins": int(actions["isin"].astype(str).nunique()),
        "etf_unique_isins": int(etf["isin"].astype(str).nunique()),
        "actions_sha256": _file_sha256(root / WEEKLY_ACTIONS),
        "etf_sha256": _file_sha256(root / WEEKLY_ETF),
        "contains_weekly_wave09_values": True,
        "daily_wave09_network_required": False,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }
    tmp = root / WEEKLY_MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(root / WEEKLY_MANIFEST)
    return payload


def run(root: Path = ROOT) -> dict:
    """Weekly post-decision bundle plus persisted Friday master snapshot."""
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="weekly-post-decision") as pool:
        fund_future = pool.submit(_measured, "ETF_FUND_FLOWS_SHADOW", etf_fund_flows_shadow_run.run, root)
        governance_future = pool.submit(_measured, "CRITERIA_GOVERNANCE", criteria_governance_audit.run, root)
        fund = fund_future.result()
        governance = governance_future.result()

    snapshot_started = time.perf_counter()
    try:
        weekly_snapshot = _persist_weekly_master_snapshot(root)
        snapshot_status = "SUCCESS"
        snapshot_error = None
    except Exception as exc:
        weekly_snapshot = {}
        snapshot_status = "FAILED"
        snapshot_error = f"{type(exc).__name__}: {str(exc)[:400]}"
    snapshot_step = {
        "step": "WEEKLY_MASTER_SNAPSHOT",
        "status": snapshot_status,
        "runtime_seconds": round(time.perf_counter() - snapshot_started, 3),
        "result_status": weekly_snapshot.get("version"),
        "error": snapshot_error,
    }

    steps = {
        "etf_fund_flows_shadow": fund,
        "criteria_governance": governance,
        "weekly_master_snapshot": snapshot_step,
    }
    if governance["status"] != "SUCCESS":
        status = "FAILED_CRITERIA_GOVERNANCE"
    elif snapshot_status != "SUCCESS":
        status = "FAILED_WEEKLY_MASTER_SNAPSHOT"
    elif fund["status"] != "SUCCESS":
        status = "SUCCESS_WITH_FUND_FLOW_WARNING"
    else:
        status = "SUCCESS_WEEKLY_POST_DECISION_BUNDLE"

    payload = {
        "status": status,
        "runtime_seconds": round(time.perf_counter() - started, 3),
        "steps": steps,
        "weekly_master_snapshot": weekly_snapshot,
        "wave09_refresh_cadence": "WEEKLY_ONLY",
        "daily_reuses_weekly_master_snapshot": True,
        "decision_brief_remains_separate_required_gate": True,
        "fund_flows_shadow_continue_on_error": True,
        "criteria_governance_required": True,
        "weekly_master_snapshot_required": True,
        "governance_overlaps_fund_flows": True,
        "external_provider_concurrency_added": False,
        "workflow_failure_semantics_changed": False,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
        "pit_logic_changed": False,
        "holdout_opened": False,
        "real_orders_enabled": False,
    }
    _write_audit(root, payload)

    if governance["status"] != "SUCCESS":
        raise RuntimeError(f"WEEKLY_POST_DECISION_BUNDLE:CRITERIA_GOVERNANCE:{governance['error']}")
    if snapshot_status != "SUCCESS":
        raise RuntimeError(f"WEEKLY_POST_DECISION_BUNDLE:WEEKLY_MASTER_SNAPSHOT:{snapshot_error}")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
