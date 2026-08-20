from __future__ import annotations

from pathlib import Path
import json

import pytest

from v182.reporting import calibration_governance_audit


def test_runtime_gate_reports_44_month_expanding_window(tmp_path: Path):
    config = tmp_path / "config"
    config.mkdir(parents=True)
    source = Path("config/CALIBRATION_WINDOWS_V21_12.json")
    (config / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    payload = calibration_governance_audit.run(tmp_path, as_of="2026-08-20")

    assert payload["status"] == "SUCCESS"
    assert payload["policy"]["primary_start"] == "2023-01-01"
    assert payload["policy"]["primary_mode"] == "EXPANDING_POST_COVID"
    assert payload["policy"]["calendar_months_touched"] == 44
    assert payload["stress_calibration_weight"] == 0.0
    assert payload["stress_parameter_optimization_allowed"] is False
    assert payload["module_specific_oos_and_holdouts_preserved"] is True
    assert payload["weight_or_threshold_changes"] is False
    assert payload["t1_t2_scope"] == "ACTION_TCT_ONLY"
    assert (tmp_path / "outputs/audit/CALIBRATION_GOVERNANCE_V21_12.json").exists()


def test_runtime_gate_reports_rolling_60_months_from_2028(tmp_path: Path):
    config = tmp_path / "config"
    config.mkdir(parents=True)
    source = Path("config/CALIBRATION_WINDOWS_V21_12.json")
    (config / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    payload = calibration_governance_audit.run(tmp_path, as_of="2028-01-01")

    assert payload["policy"]["primary_mode"] == "ROLLING_60_MONTHS"
    assert payload["policy"]["rolling_months"] == 60
    assert payload["policy"]["primary_start"] == "2023-01-01"


def test_runtime_gate_fails_closed_on_policy_drift(tmp_path: Path):
    config = tmp_path / "config"
    config.mkdir(parents=True)
    policy = json.loads(Path("config/CALIBRATION_WINDOWS_V21_12.json").read_text(encoding="utf-8"))
    policy["stress_library"]["calibration_weight"] = 0.1
    (config / "CALIBRATION_WINDOWS_V21_12.json").write_text(
        json.dumps(policy), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="STRESS_CALIBRATION_WEIGHT"):
        calibration_governance_audit.run(tmp_path, as_of="2026-08-20")


def test_runtime_gate_fails_closed_if_module_holdout_lock_is_removed(tmp_path: Path):
    config = tmp_path / "config"
    config.mkdir(parents=True)
    policy = json.loads(Path("config/CALIBRATION_WINDOWS_V21_12.json").read_text(encoding="utf-8"))
    policy["governance"]["final_holdout_remains_module_specific_and_locked"] = False
    (config / "CALIBRATION_WINDOWS_V21_12.json").write_text(
        json.dumps(policy), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="MODULE_HOLDOUT_GOVERNANCE_DRIFT"):
        calibration_governance_audit.run(tmp_path, as_of="2026-08-20")
