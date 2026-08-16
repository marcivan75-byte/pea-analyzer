import json
from pathlib import Path

from v182.reporting.sector_rotation_v2_committee_bridge import build_committee_sector_rotation_v2_status
from v182.reporting.unified_runner import _sector_validation_from_step


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_committee_bridge_surfaces_shadow_diagnostics_without_decision_influence(tmp_path: Path):
    _write_json(
        tmp_path / "outputs" / "audit" / "V2_SECTOR_ROTATION_SHADOW.json",
        {
            "decision_influence": 0.0,
            "promising_but_overvalued": ["Technology"],
            "correction_alerts": ["Semiconductors"],
            "priority_candidates": ["Energy"],
            "reentry_ready": ["Software"],
        },
    )
    _write_json(
        tmp_path / "outputs" / "audit" / "V2_SECTOR_ROTATION_PIT_OOS_STATUS.json",
        {
            "status": "WAIT_FOR_PIT_HISTORY",
            "protocol_version": "TEST_PROTOCOL",
            "primary_horizon_days": 60,
            "holdout_locked": True,
            "pre_holdout_pass": False,
            "promotion_ready": False,
            "decision_influence": 0.0,
            "warning_gate": {"pass": False},
            "periods": {"VALIDATION_OOS": {"pass": False}},
        },
    )

    status = build_committee_sector_rotation_v2_status(tmp_path)

    assert status["status"] == "ACTIVE_SHADOW_DIAGNOSTIC"
    assert status["pit_oos_status"] == "WAIT_FOR_PIT_HISTORY"
    assert status["decision_influence"] == 0.0
    assert status["active_in_final_decisions"] is False
    assert status["promotion_ready"] is False
    assert status["promising_but_overvalued"] == ["Technology"]
    assert status["correction_alerts"] == ["Semiconductors"]


def test_committee_bridge_blocks_unexpected_promotion_or_nonzero_influence(tmp_path: Path):
    _write_json(
        tmp_path / "outputs" / "audit" / "V2_SECTOR_ROTATION_PIT_OOS_STATUS.json",
        {
            "status": "UNEXPECTED",
            "promotion_ready": True,
            "decision_influence": 0.1,
        },
    )

    status = build_committee_sector_rotation_v2_status(tmp_path)

    assert status["status"] == "GOVERNANCE_BREACH_BLOCKED"
    assert status["promotion_ready"] is False
    assert status["decision_influence"] == 0.0
    assert status["active_in_final_decisions"] is False


def test_unified_summary_extracts_sector_pit_oos_validation():
    step = {
        "status": "SUCCESS",
        "result": {
            "pit_oos_validation": {
                "status": "WAIT_FOR_PIT_HISTORY",
                "promotion_ready": False,
                "decision_influence": 0.0,
            }
        },
    }
    validation = _sector_validation_from_step(step)
    assert validation["status"] == "WAIT_FOR_PIT_HISTORY"
    assert validation["promotion_ready"] is False
    assert validation["decision_influence"] == 0.0


def test_unified_summary_fails_closed_when_sector_step_is_unavailable():
    validation = _sector_validation_from_step({"status": "FAILED"})
    assert validation == {"status": "UNAVAILABLE", "promotion_ready": False, "decision_influence": 0.0}
