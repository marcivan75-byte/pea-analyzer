from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY_PATH = ROOT / "config" / "CALIBRATION_WINDOWS_V21_11.json"


@dataclass(frozen=True)
class CalibrationWindow:
    start: pd.Timestamp
    end: pd.Timestamp
    mode: str
    rolling_months: int | None


@dataclass(frozen=True)
class CalibrationSplit:
    primary: pd.DataFrame
    stress: pd.DataFrame
    outside: pd.DataFrame
    window: CalibrationWindow


def _utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("INVALID_CALIBRATION_TIMESTAMP")
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("CALIBRATION_POLICY_MUST_BE_OBJECT")
    validate_policy(payload)
    return payload


def validate_policy(policy: Mapping[str, Any]) -> None:
    primary = policy.get("primary_calibration") or {}
    stress_library = policy.get("stress_library") or {}
    governance = policy.get("governance") or {}

    anchor = _utc_timestamp(primary.get("start"))
    activation = _utc_timestamp(primary.get("rolling_activation_date"))
    rolling_months = int(primary.get("rolling_months", 0))
    if anchor >= activation:
        raise ValueError("PRIMARY_ANCHOR_MUST_PRECEDE_ROLLING_ACTIVATION")
    if rolling_months <= 0:
        raise ValueError("ROLLING_MONTHS_MUST_BE_POSITIVE")
    if float(primary.get("weight", -1.0)) != 1.0:
        raise ValueError("PRIMARY_CALIBRATION_WEIGHT_MUST_BE_ONE")
    if float(stress_library.get("calibration_weight", 1.0)) != 0.0:
        raise ValueError("STRESS_CALIBRATION_WEIGHT_MUST_BE_ZERO")
    if stress_library.get("optimization_allowed") is not False:
        raise ValueError("STRESS_OPTIMIZATION_MUST_BE_FORBIDDEN")
    if stress_library.get("parameter_retuning_from_stress_results") is not False:
        raise ValueError("STRESS_RETUNING_MUST_BE_FORBIDDEN")
    if governance.get("stress_rows_may_enter_primary_calibration") is not False:
        raise ValueError("STRESS_ROWS_MUST_BE_FORBIDDEN_FROM_PRIMARY")
    if governance.get("pit_required") is not True or governance.get("anti_lookahead_required") is not True:
        raise ValueError("PIT_AND_ANTI_LOOKAHEAD_MUST_REMAIN_REQUIRED")

    periods = stress_library.get("periods") or []
    if not periods:
        raise ValueError("STRESS_LIBRARY_REQUIRES_AT_LEAST_ONE_PERIOD")
    ordered: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for period in periods:
        start = _utc_timestamp(period.get("start"))
        end = _utc_timestamp(period.get("end"))
        if start > end:
            raise ValueError("STRESS_PERIOD_START_AFTER_END")
        if end >= anchor:
            raise ValueError("STRESS_PERIOD_OVERLAPS_PRIMARY_ANCHOR")
        ordered.append((start, end))
    ordered.sort()
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] <= previous[1]:
            raise ValueError("OVERLAPPING_STRESS_PERIODS")


def resolve_primary_window(as_of: Any, policy: Mapping[str, Any]) -> CalibrationWindow:
    validate_policy(policy)
    end = _utc_timestamp(as_of)
    primary = policy["primary_calibration"]
    anchor = _utc_timestamp(primary["start"])
    activation = _utc_timestamp(primary["rolling_activation_date"])
    rolling_months = int(primary["rolling_months"])
    if end < anchor:
        raise ValueError("AS_OF_PRECEDES_POST_COVID_CALIBRATION_ANCHOR")
    if end < activation:
        return CalibrationWindow(
            start=anchor,
            end=end,
            mode=str(primary["mode_before_2028"]),
            rolling_months=None,
        )
    return CalibrationWindow(
        start=end - pd.DateOffset(months=rolling_months),
        end=end,
        mode=str(primary["mode_from_2028"]),
        rolling_months=rolling_months,
    )


def calendar_months_touched(start: Any, end: Any) -> int:
    left = _utc_timestamp(start)
    right = _utc_timestamp(end)
    if right < left:
        raise ValueError("END_PRECEDES_START")
    return (right.year - left.year) * 12 + (right.month - left.month) + 1


def _stress_mask(dates: pd.Series, policy: Mapping[str, Any]) -> pd.Series:
    mask = pd.Series(False, index=dates.index, dtype=bool)
    for period in policy["stress_library"]["periods"]:
        start = _utc_timestamp(period["start"])
        end = _utc_timestamp(period["end"])
        mask |= dates.between(start, end, inclusive="both")
    return mask


def split_frame(
    frame: pd.DataFrame,
    *,
    date_col: str,
    as_of: Any,
    policy: Mapping[str, Any],
) -> CalibrationSplit:
    """Partition historical rows without allowing stress data into calibration.

    Rows in 2020-2022 are retained in the stress partition with calibration
    influence zero. Rows after ``as_of`` and rows outside all governed windows
    are placed in ``outside``. No row is imputed or re-dated.
    """
    if date_col not in frame.columns:
        raise ValueError(f"MISSING_CALIBRATION_DATE_COLUMN:{date_col}")
    validate_policy(policy)
    window = resolve_primary_window(as_of, policy)
    prepared = frame.copy()
    prepared[date_col] = pd.to_datetime(prepared[date_col], errors="coerce", utc=True)

    valid_date = prepared[date_col].notna()
    primary_mask = valid_date & prepared[date_col].between(window.start, window.end, inclusive="both")
    stress_mask = valid_date & _stress_mask(prepared[date_col], policy)
    if bool((primary_mask & stress_mask).any()):
        raise RuntimeError("CALIBRATION_STRESS_WINDOW_OVERLAP")

    primary = prepared.loc[primary_mask].copy()
    stress = prepared.loc[stress_mask].copy()
    outside = prepared.loc[~(primary_mask | stress_mask)].copy()
    return CalibrationSplit(primary=primary, stress=stress, outside=outside, window=window)


def assert_primary_calibration_frame(
    frame: pd.DataFrame,
    *,
    date_col: str,
    as_of: Any,
    policy: Mapping[str, Any],
) -> CalibrationWindow:
    """Fail closed unless every row belongs to the governed primary window."""
    split = split_frame(frame, date_col=date_col, as_of=as_of, policy=policy)
    if not split.stress.empty:
        raise ValueError("STRESS_ROWS_FORBIDDEN_IN_PRIMARY_CALIBRATION")
    if not split.outside.empty:
        future = split.outside[date_col].notna() & (split.outside[date_col] > split.window.end)
        if bool(future.any()):
            raise ValueError("FUTURE_ROWS_FORBIDDEN_IN_PRIMARY_CALIBRATION")
        raise ValueError("ROWS_OUTSIDE_PRIMARY_CALIBRATION_WINDOW")
    return split.window


def governance_summary(as_of: Any, policy: Mapping[str, Any]) -> dict[str, Any]:
    window = resolve_primary_window(as_of, policy)
    stress_periods = [
        {"id": period["id"], "start": period["start"], "end": period["end"]}
        for period in policy["stress_library"]["periods"]
    ]
    return {
        "policy_version": policy.get("version"),
        "primary_start": window.start.date().isoformat(),
        "primary_end": window.end.date().isoformat(),
        "primary_mode": window.mode,
        "calendar_months_touched": calendar_months_touched(window.start, window.end),
        "rolling_months": window.rolling_months,
        "stress_calibration_weight": float(policy["stress_library"]["calibration_weight"]),
        "stress_optimization_allowed": bool(policy["stress_library"]["optimization_allowed"]),
        "stress_periods": stress_periods,
        "pit_required": bool(policy["governance"]["pit_required"]),
        "anti_lookahead_required": bool(policy["governance"]["anti_lookahead_required"]),
    }
