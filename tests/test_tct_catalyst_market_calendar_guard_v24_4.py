from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from v182.features.tct_catalyst_context_v24_4 import catalyst_window
from v182.reporting.tct_next_session_catalyst_run import _seed_staleness_days


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_4_0_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))


def test_preopen_uses_real_seed_date_across_monday_market_holiday():
    cfg = _cfg()
    # Tuesday 25 August after a hypothetical Monday exchange holiday. The
    # latest completed daily seed is Friday 21 August, so the news window must
    # start Friday close rather than Monday close.
    now = datetime(2026, 8, 25, 6, 40, tzinfo=timezone.utc)
    start, end = catalyst_window("PREOPEN", now, cfg, anchor_date="2026-08-21")
    assert start == datetime(2026, 8, 21, 15, 30, tzinfo=timezone.utc)
    assert end == now


def test_future_or_same_day_seed_is_never_used_as_preopen_anchor():
    cfg = _cfg()
    now = datetime(2026, 8, 25, 6, 40, tzinfo=timezone.utc)
    start, _ = catalyst_window("PREOPEN", now, cfg, anchor_date="2026-08-25")
    assert start == datetime(2026, 8, 24, 15, 30, tzinfo=timezone.utc)


def test_seed_staleness_is_measured_in_local_calendar_days():
    cfg = _cfg()
    now = datetime(2026, 8, 25, 6, 40, tzinfo=timezone.utc)
    assert _seed_staleness_days("2026-08-21", now, cfg) == 4
    assert _seed_staleness_days("2026-08-24", now, cfg) == 1
