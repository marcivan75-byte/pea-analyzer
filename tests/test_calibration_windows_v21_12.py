from __future__ import annotations

import pandas as pd
import pytest

from v182.backtest.calibration_windows import (
    assert_primary_calibration_frame,
    calendar_months_touched,
    governance_summary,
    load_policy,
    resolve_primary_window,
    split_frame,
)


def _policy():
    return load_policy()


def test_2026_primary_window_is_expanding_post_covid_and_touches_44_months():
    window = resolve_primary_window("2026-08-20", _policy())
    assert window.start == pd.Timestamp("2023-01-01", tz="UTC")
    assert window.end == pd.Timestamp("2026-08-20", tz="UTC")
    assert window.mode == "EXPANDING_POST_COVID"
    assert window.rolling_months is None
    assert calendar_months_touched(window.start, window.end) == 44


def test_rolling_60_month_window_activates_in_january_2028():
    policy = _policy()
    at_activation = resolve_primary_window("2028-01-01", policy)
    later = resolve_primary_window("2028-08-20", policy)

    assert at_activation.mode == "ROLLING_60_MONTHS"
    assert at_activation.rolling_months == 60
    assert at_activation.start == pd.Timestamp("2023-01-01", tz="UTC")
    assert later.start == pd.Timestamp("2023-08-20", tz="UTC")
    assert later.end == pd.Timestamp("2028-08-20", tz="UTC")


def test_split_keeps_covid_stress_library_out_of_primary_calibration():
    frame = pd.DataFrame(
        {
            "date": [
                "2019-12-31",
                "2020-03-16",
                "2021-11-26",
                "2022-12-30",
                "2023-01-02",
                "2025-04-07",
                "2026-08-20",
                "2026-08-21",
            ],
            "value": range(8),
        }
    )
    split = split_frame(frame, date_col="date", as_of="2026-08-20", policy=_policy())

    assert split.primary["value"].tolist() == [4, 5, 6]
    assert split.stress["value"].tolist() == [1, 2, 3]
    assert split.outside["value"].tolist() == [0, 7]
    assert set(split.primary["date"].dt.year) >= {2023}
    assert set(split.stress["date"].dt.year) == {2020, 2021, 2022}


def test_stress_end_date_includes_the_entire_last_calendar_day():
    frame = pd.DataFrame(
        {
            "date": [
                "2022-12-31 00:00:00+00:00",
                "2022-12-31 23:59:59+00:00",
                "2023-01-01 00:00:00+00:00",
            ],
            "value": [1, 2, 3],
        }
    )
    split = split_frame(frame, date_col="date", as_of="2026-08-20", policy=_policy())

    assert split.stress["value"].tolist() == [1, 2]
    assert split.primary["value"].tolist() == [3]
    assert split.outside.empty


def test_primary_calibration_fails_closed_if_stress_rows_are_mixed_in():
    frame = pd.DataFrame({"date": ["2021-06-01", "2024-06-01"], "score": [1.0, 2.0]})
    with pytest.raises(ValueError, match="STRESS_ROWS_FORBIDDEN_IN_PRIMARY_CALIBRATION"):
        assert_primary_calibration_frame(
            frame,
            date_col="date",
            as_of="2026-08-20",
            policy=_policy(),
        )


def test_primary_calibration_fails_closed_on_future_rows():
    frame = pd.DataFrame({"date": ["2024-06-01", "2026-08-21"], "score": [1.0, 2.0]})
    with pytest.raises(ValueError, match="FUTURE_ROWS_FORBIDDEN_IN_PRIMARY_CALIBRATION"):
        assert_primary_calibration_frame(
            frame,
            date_col="date",
            as_of="2026-08-20",
            policy=_policy(),
        )


def test_governance_summary_records_zero_stress_calibration_weight():
    summary = governance_summary("2026-08-20", _policy())
    assert summary["policy_version"] == "V21.12_CALIBRATION_WINDOWS_2026_08_20"
    assert summary["calendar_months_touched"] == 44
    assert summary["stress_calibration_weight"] == 0.0
    assert summary["stress_optimization_allowed"] is False
    assert summary["pit_required"] is True
    assert summary["anti_lookahead_required"] is True
    assert summary["stress_periods"] == [
        {
            "id": "COVID_AND_POST_COVID_STRESS_2020_2022",
            "start": "2020-01-01",
            "end": "2022-12-31",
        }
    ]


def test_policy_rejects_stress_overlap_with_primary_anchor():
    policy = _policy()
    policy["stress_library"]["periods"][0]["end"] = "2023-01-01"
    with pytest.raises(ValueError, match="STRESS_PERIOD_OVERLAPS_PRIMARY_ANCHOR"):
        resolve_primary_window("2026-08-20", policy)
