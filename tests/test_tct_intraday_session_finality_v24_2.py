from __future__ import annotations

import json
from pathlib import Path

from v182.reporting.tct_intraday_shadow_run import _eligible_completed_sessions


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_2_0_INTRADAY_SHADOW.json").read_text(encoding="utf-8"))


def test_current_calendar_session_is_deferred_not_persisted():
    cfg = _cfg()
    sessions = ["2026-08-18", "2026-08-19"]
    eligible = _eligible_completed_sessions(
        sessions,
        "2026-08-18",
        cfg,
        as_of_date="2026-08-19",
    )
    assert eligible == []


def test_deferred_j1_becomes_eligible_on_next_calendar_day():
    cfg = _cfg()
    sessions = ["2026-08-18", "2026-08-19", "2026-08-20"]
    eligible = _eligible_completed_sessions(
        sessions,
        "2026-08-18",
        cfg,
        as_of_date="2026-08-20",
    )
    assert eligible == ["2026-08-19"]


def test_only_first_three_completed_post_signal_sessions_are_eligible():
    cfg = _cfg()
    sessions = [
        "2026-08-18",
        "2026-08-19",
        "2026-08-20",
        "2026-08-21",
        "2026-08-24",
        "2026-08-25",
    ]
    eligible = _eligible_completed_sessions(
        sessions,
        "2026-08-18",
        cfg,
        as_of_date="2026-08-25",
    )
    assert eligible == ["2026-08-19", "2026-08-20", "2026-08-21"]


def test_config_explicitly_requires_completed_session_persistence():
    bridge = _cfg()["signal_bridge"]
    assert bridge["completed_sessions_only"] is True
    assert bridge["current_calendar_session_persistence_forbidden"] is True
    assert bridge["same_session_execution_forbidden"] is True
