from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

import pandas as pd

from v182.io.frames import apply_observations, is_missing, save_master
from v182.reporting.daily_context_baseline import publish_from_outputs
from v182.state.etf_structure_state import load_replay_observations, load_state_config

ROOT = Path(__file__).resolve().parents[3]
REFRESH_AUDIT_RELATIVE = Path("outputs/audit/V21_10_ETF_STRUCTURAL_DATA.json")


def _coverage(frame: pd.DataFrame, field: str) -> float:
    if field not in frame.columns or frame.empty:
        return 0.0
    return round(float((~frame[field].apply(is_missing)).mean() * 100.0), 2)


def _same_github_run_refresh(root: Path) -> dict | None:
    """Return the successful structural refresh audit for this exact Actions run.

    GitHub re-runs keep the same run id but increment the attempt number, so both
    identifiers are matched when available. Local/manual execution has no run id
    and therefore always follows the historical replay path.
    """
    run_id=str(os.environ.get("GITHUB_RUN_ID") or "").strip()
    if not run_id:
        return None
    path=root/REFRESH_AUDIT_RELATIVE
    if not path.exists():
        return None
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError,TypeError):
        return None
    if payload.get("status") != "SUCCESS" or str(payload.get("github_run_id") or "").strip() != run_id:
        return None
    attempt=str(os.environ.get("GITHUB_RUN_ATTEMPT") or "").strip()
    if attempt and str(payload.get("github_run_attempt") or "").strip() != attempt:
        return None
    return payload


def _publish_weekly_daily_fast_baseline(root: Path, audit: dict) -> None:
    """Seed the Mon-Thu fast path only from a full weekly context.

    DAILY_TACTICAL executions are explicitly forbidden from advancing the full
    slow-source timestamp. A publication failure is visible in the replay audit
    but does not rewrite the underlying ETF structural state result.
    """
    if os.environ.get("PEA_RUN_PROFILE", "").strip().upper() == "DAILY_TACTICAL":
        audit["daily_fast_baseline"] = {"status": "SKIPPED_DAILY_PROFILE"}
        return
    try:
        baseline = publish_from_outputs(root, profile="WEEKLY_FULL_COMMITTEE_POST_ETF_REPLAY")
        audit["daily_fast_baseline"] = {"status": "PUBLISHED", **baseline}
    except Exception as exc:
        audit["daily_fast_baseline"] = {
            "status": "FAILED_NON_BLOCKING",
            "error": type(exc).__name__,
            "detail": str(exc)[:240],
        }


def _write_already_applied_audit(root: Path, config: dict, refresh: dict) -> dict:
    coverage=dict(refresh.get("coverage_pct") or {})
    audit = {
        "version": config.get("version"),
        "status": "SUCCESS",
        "execution_mode": "SKIPPED_ALREADY_APPLIED_CURRENT_GITHUB_RUN",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "github_run_id": str(os.environ.get("GITHUB_RUN_ID") or ""),
        "github_run_attempt": str(os.environ.get("GITHUB_RUN_ATTEMPT") or ""),
        "canonical_universe_count": 102,
        "state": {
            "status": "CURRENT_WEEKLY_REFRESH_ALREADY_APPLIED",
            "refresh_version": refresh.get("version"),
            "refresh_generated_at_utc": refresh.get("generated_at_utc"),
            "changed_cells": refresh.get("changed_cells"),
        },
        "replay_observations": 0,
        "merge_quarantined": 0,
        "coverage_before_pct": coverage,
        "coverage_after_pct": coverage,
        "governance": config.get("governance", {}),
    }
    _publish_weekly_daily_fast_baseline(root, audit)
    audit_path = root / str(config["audit_replay_path"])
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2, default=str))
    return audit


def run(root: Path = ROOT) -> dict:
    config = load_state_config(root / "config" / "ETF_STRUCTURE_STATE_V21_15.json")
    refresh=_same_github_run_refresh(root)
    if refresh is not None:
        # The weekly refresh already replayed governed state before collecting new
        # structure, wrote the current enriched master, and persisted a new state
        # snapshot. Replaying that snapshot immediately again would only create
        # duplicate KEEP/provenance work for the Friday tactical consumers.
        return _write_already_applied_audit(root,config,refresh)

    master_path = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not master_path.exists() or master_path.stat().st_size == 0:
        raise FileNotFoundError("ETF_STRUCTURE_STATE_REPLAY_REQUIRES_CURRENT_ENRICHED_MASTER")
    frame = pd.read_csv(master_path, sep=";", encoding="utf-8-sig", low_memory=False)
    if len(frame) != 102 or frame.get("isin", pd.Series(dtype=str)).nunique() != 102:
        raise RuntimeError(f"ETF_STRUCTURE_STATE_REPLAY_CANONICAL_UNIVERSE_REQUIRED:{len(frame)}")

    fields = tuple((config.get("fields") or {}).keys())
    before = {field: _coverage(frame, field) for field in fields}
    observations, state_diag = load_replay_observations(config, root=root)
    quarantined: list[dict] = []
    if observations:
        frame, quarantined = apply_observations(frame, observations)
        save_master(frame, master_path)
    after = {field: _coverage(frame, field) for field in fields}

    audit = {
        "version": config.get("version"),
        "status": "SUCCESS" if state_diag.get("status") in {"SUCCESS", "NO_STATE", "NO_ELIGIBLE_STATE_ROWS"} else "STATE_INVALID_FAIL_CLOSED",
        "execution_mode": "REPLAY_EXECUTED",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "github_run_id": str(os.environ.get("GITHUB_RUN_ID") or ""),
        "github_run_attempt": str(os.environ.get("GITHUB_RUN_ATTEMPT") or ""),
        "canonical_universe_count": 102,
        "state": state_diag,
        "replay_observations": int(len(observations)),
        "merge_quarantined": int(len(quarantined)),
        "coverage_before_pct": before,
        "coverage_after_pct": after,
        "governance": config.get("governance", {}),
    }
    _publish_weekly_daily_fast_baseline(root, audit)
    audit_path = root / str(config["audit_replay_path"])
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2, default=str))
    return audit


if __name__ == "__main__":
    run()
