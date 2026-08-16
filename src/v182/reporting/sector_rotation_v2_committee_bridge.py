from __future__ import annotations

from pathlib import Path
from typing import Any
import json


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_committee_sector_rotation_v2_status(root: Path) -> dict[str, Any]:
    """Expose Sector Rotation V2 to the Committee as diagnostics only.

    This bridge deliberately does not read or write COMMITTEE_DECISIONS.csv and
    therefore cannot alter Action/ETF scores, BUY/SELL decisions, sizing or orders.
    """
    shadow = _read_json(root / "outputs" / "audit" / "V2_SECTOR_ROTATION_SHADOW.json")
    pit = _read_json(root / "outputs" / "audit" / "V2_SECTOR_ROTATION_PIT_OOS_STATUS.json")

    pit_status = str(pit.get("status") or "WAIT_FOR_PIT_HISTORY")
    promotion_ready = bool(pit.get("promotion_ready", False))
    decision_influence = float(pit.get("decision_influence", shadow.get("decision_influence", 0.0)) or 0.0)
    if promotion_ready or decision_influence != 0.0:
        return {
            "status": "GOVERNANCE_BREACH_BLOCKED",
            "mode": "SHADOW_ONLY",
            "pit_oos_status": pit_status,
            "promotion_ready": False,
            "decision_influence": 0.0,
            "active_in_final_decisions": False,
            "reason": "Sector Rotation V2 may not influence final decisions before governed promotion.",
        }

    warning_gate = pit.get("warning_gate") if isinstance(pit.get("warning_gate"), dict) else {}
    outcome_diagnostic = pit.get("outcome_diagnostic") if isinstance(pit.get("outcome_diagnostic"), dict) else {}
    periods = pit.get("periods") if isinstance(pit.get("periods"), dict) else {}

    return {
        "status": "ACTIVE_SHADOW_DIAGNOSTIC",
        "mode": "SHADOW_ONLY",
        "pit_oos_status": pit_status,
        "protocol_version": pit.get("protocol_version"),
        "primary_horizon_days": pit.get("primary_horizon_days"),
        "holdout_locked": bool(pit.get("holdout_locked", True)),
        "pre_holdout_pass": bool(pit.get("pre_holdout_pass", False)),
        "promotion_ready": False,
        "decision_influence": 0.0,
        "active_in_final_decisions": False,
        "automatic_weight_change_allowed": False,
        "automatic_threshold_retuning_allowed": False,
        "promising_but_overvalued": list(shadow.get("promising_but_overvalued") or []),
        "correction_alerts": list(shadow.get("correction_alerts") or []),
        "priority_candidates_shadow_only": list(shadow.get("priority_candidates") or []),
        "reentry_ready_shadow_only": list(shadow.get("reentry_ready") or []),
        "warning_gate": warning_gate,
        "periods": periods,
        "outcome_diagnostic": outcome_diagnostic,
        "outputs": {
            "sector_snapshot": "outputs/sector_rotation/V2_SECTOR_ROTATION_SHADOW.csv",
            "shadow_audit": "outputs/audit/V2_SECTOR_ROTATION_SHADOW.json",
            "pit_oos_status": "outputs/audit/V2_SECTOR_ROTATION_PIT_OOS_STATUS.json",
            "pit_oos_observations": "outputs/sector_rotation/V2_PIT_OOS_OBSERVATIONS.csv",
            "pit_oos_snapshot_metrics": "outputs/sector_rotation/V2_PIT_OOS_SNAPSHOT_METRICS.csv",
            "frozen_constituents": "state/sector_rotation_v2/SECTOR_ROTATION_V2_CONSTITUENTS.csv",
        },
    }
