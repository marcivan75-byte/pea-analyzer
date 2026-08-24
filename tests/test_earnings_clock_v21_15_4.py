from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from v182.reporting.earnings_clock_v21_15_4 import refresh_frame


def _epoch(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


def test_earnings_clock_recomputes_relative_fields_without_touching_source_timestamp() -> None:
    now = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
    future = _epoch("2026-08-28T08:00:00")
    frame = pd.DataFrame(
        [{
            "isin": "A1",
            "next_earnings_timestamp_yf": future,
            "days_to_earnings": 20.0,
            "earnings_within_7d_flag": 0.0,
            "earnings_within_30d_flag": 1.0,
        }]
    )
    refreshed, audit = refresh_frame(frame, now=now)
    assert float(refreshed.loc[0, "days_to_earnings"]) == 5.0
    assert float(refreshed.loc[0, "earnings_within_7d_flag"]) == 1.0
    assert float(refreshed.loc[0, "earnings_within_30d_flag"]) == 1.0
    assert int(refreshed.loc[0, "next_earnings_timestamp_yf"]) == future
    assert audit["network_calls"] == 0
    assert audit["source_timestamp_changed"] is False


def test_past_earnings_timestamp_cannot_remain_hot() -> None:
    now = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
    past = _epoch("2026-08-20T08:00:00")
    frame = pd.DataFrame(
        [{
            "isin": "A1",
            "next_earnings_timestamp_yf": past,
            "days_to_earnings": 1.0,
            "earnings_within_7d_flag": 1.0,
            "earnings_within_30d_flag": 1.0,
        }]
    )
    refreshed, audit = refresh_frame(frame, now=now)
    assert float(refreshed.loc[0, "days_to_earnings"]) == -3.0
    assert float(refreshed.loc[0, "earnings_within_7d_flag"]) == 0.0
    assert float(refreshed.loc[0, "earnings_within_30d_flag"]) == 0.0
    assert audit["past_event_timestamps"] == 1


def test_missing_source_timestamp_does_not_invent_calendar_evidence() -> None:
    frame = pd.DataFrame([{"isin": "A1", "days_to_earnings": 4.0}])
    refreshed, audit = refresh_frame(frame, now=datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc))
    assert refreshed.equals(frame)
    assert audit["rows_recomputed"] == 0
