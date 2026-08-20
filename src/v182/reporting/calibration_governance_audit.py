from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import argparse
import json

from v182.backtest.calibration_windows import governance_summary, load_policy

ROOT = Path(__file__).resolve().parents[3]
POLICY_RELATIVE_PATH = Path("config/CALIBRATION_WINDOWS_V21_12.json")
OUTPUT_RELATIVE_PATH = Path("outputs/audit/CALIBRATION_GOVERNANCE_V21_12.json")
EXPECTED_POLICY_VERSION = "V21.12_CALIBRATION_WINDOWS_2026_08_20"


def _assert_frozen_reference(policy: dict[str, Any]) -> None:
    primary = policy.get("primary_calibration") or {}
    stress = policy.get("stress_library") or {}
    governance = policy.get("governance") or {}
    periods = stress.get("periods") or []

    if policy.get("version") != EXPECTED_POLICY_VERSION:
        raise ValueError("CALIBRATION_POLICY_VERSION_DRIFT")
    if primary.get("start") != "2023-01-01":
        raise ValueError("PRIMARY_CALIBRATION_START_DRIFT")
    if primary.get("mode_before_2028") != "EXPANDING_POST_COVID":
        raise ValueError("PRIMARY_PRE_2028_MODE_DRIFT")
    if primary.get("mode_from_2028") != "ROLLING_60_MONTHS":
        raise ValueError("PRIMARY_POST_2028_MODE_DRIFT")
    if primary.get("rolling_activation_date") != "2028-01-01":
        raise ValueError("ROLLING_ACTIVATION_DATE_DRIFT")
    if int(primary.get("rolling_months", 0)) != 60:
        raise ValueError("ROLLING_MONTHS_DRIFT")
    if float(primary.get("weight", -1.0)) != 1.0:
        raise ValueError("PRIMARY_CALIBRATION_WEIGHT_DRIFT")

    if float(stress.get("calibration_weight", 1.0)) != 0.0:
        raise ValueError("STRESS_CALIBRATION_WEIGHT_DRIFT")
    if stress.get("optimization_allowed") is not False:
        raise ValueError("STRESS_OPTIMIZATION_DRIFT")
    if stress.get("parameter_retuning_from_stress_results") is not False:
        raise ValueError("STRESS_RETUNING_DRIFT")
    if len(periods) != 1:
        raise ValueError("STRESS_LIBRARY_PERIOD_COUNT_DRIFT")
    period = periods[0]
    if period.get("start") != "2020-01-01" or period.get("end") != "2022-12-31":
        raise ValueError("STRESS_LIBRARY_PERIOD_DRIFT")

    if governance.get("stress_rows_may_enter_primary_calibration") is not False:
        raise ValueError("STRESS_PRIMARY_ISOLATION_DRIFT")
    if governance.get("stress_results_may_optimize_normal_weights") is not False:
        raise ValueError("STRESS_WEIGHT_OPTIMIZATION_DRIFT")
    if governance.get("stress_results_may_optimize_normal_thresholds") is not False:
        raise ValueError("STRESS_THRESHOLD_OPTIMIZATION_DRIFT")
    if governance.get("pit_required") is not True:
        raise ValueError("PIT_REQUIREMENT_DRIFT")
    if governance.get("anti_lookahead_required") is not True:
        raise ValueError("ANTI_LOOKAHEAD_REQUIREMENT_DRIFT")
    if governance.get("final_holdout_remains_module_specific_and_locked") is not True:
        raise ValueError("MODULE_HOLDOUT_GOVERNANCE_DRIFT")


def run(root: Path = ROOT, as_of: Any | None = None) -> dict[str, Any]:
    policy_path = root / POLICY_RELATIVE_PATH
    policy = load_policy(policy_path)
    _assert_frozen_reference(policy)

    resolved_as_of = as_of or datetime.now(timezone.utc).date().isoformat()
    summary = governance_summary(resolved_as_of, policy)
    payload: dict[str, Any] = {
        "status": "SUCCESS",
        "version": "V21.12.2_CALIBRATION_RUNTIME_GOVERNANCE",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of": str(resolved_as_of),
        "policy_path": str(POLICY_RELATIVE_PATH),
        "policy": summary,
        "enforcement_role": "GOVERNANCE_GATE_ONLY",
        "ordinary_calibration_contract": "USE_GOVERNED_PRIMARY_WINDOW_AND_KEEP_STRESS_SEPARATE",
        "ordinary_calibration_must_use_fail_closed_window_validation": True,
        "stress_decision_influence": 0.0,
        "stress_calibration_weight": 0.0,
        "stress_parameter_optimization_allowed": False,
        "module_specific_oos_and_holdouts_preserved": True,
        "module_specific_protocols_take_precedence_over_generic_window_filtering": True,
        "weight_or_threshold_changes": False,
        "entry_exit_changes": False,
        "holdout_unlocked": False,
        "t1_t2_scope": "ACTION_TCT_ONLY",
        "real_orders_enabled": False,
    }

    out = root / OUTPUT_RELATIVE_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["output"] = str(OUTPUT_RELATIVE_PATH)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit V21.12 calibration-window governance at runtime")
    parser.add_argument("--as-of", default=None, help="PIT date/timestamp override for deterministic validation")
    args = parser.parse_args()
    print(json.dumps(run(as_of=args.as_of), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
