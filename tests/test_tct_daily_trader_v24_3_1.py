from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.features.tct_daily_trader_v24_3_1 import compute_daily_weekly_trader_snapshot
from v182.reporting.tct_daily_trader_shadow_run_v24_3_1 import _completed_daily_history


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_3_1_DAILY_TRADER_SHADOW.json").read_text(encoding="utf-8"))


def _history(periods: int = 110) -> pd.DataFrame:
    idx = pd.bdate_range("2026-03-16", periods=periods)
    close = np.linspace(90.0, 106.0, len(idx))
    volume = np.full(len(idx), 1_000_000.0)
    return pd.DataFrame(
        {
            "open": close - 0.20,
            "high": close + 0.45,
            "low": close - 0.45,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )


def test_v2431_remains_daily_only_and_adds_confluence():
    frame = _history()
    frame.iloc[-1, frame.columns.get_loc("open")] = 106.2
    frame.iloc[-1, frame.columns.get_loc("high")] = 108.4
    frame.iloc[-1, frame.columns.get_loc("low")] = 106.0
    frame.iloc[-1, frame.columns.get_loc("close")] = 108.0
    frame.iloc[-1, frame.columns.get_loc("volume")] = 2_200_000.0
    snap = compute_daily_weekly_trader_snapshot(frame, _cfg())
    assert snap["status"] == "SUCCESS_SHADOW"
    assert snap["intraday_data_used"] is False
    assert snap["new_market_data_downloads_required"] is False
    assert snap["entry_confirmation_count"] >= 2
    assert isinstance(snap["entry_trigger_confirmed"], bool)
    assert snap["trend_efficiency_20d"] is not None


def test_v2431_does_not_treat_partial_week_as_completed_week():
    frame = _history(108)
    # Force the evaluation date to a Thursday; current weekly group is partial.
    while frame.index[-1].weekday() != 3:
        frame = frame.iloc[:-1]
    snap = compute_daily_weekly_trader_snapshot(frame, _cfg())
    assert snap["current_week_complete"] is False
    assert snap["current_week_close"] is not None
    assert snap["weekly_close"] is not None
    assert snap["weekly_close"] != snap["current_week_close"]


def test_v2431_persists_recent_breakout_level_and_detects_failure():
    frame = _history()
    # Create a clear breakout three sessions before the evaluation date.
    breakout_pos = len(frame) - 4
    prior_high = float(frame["high"].iloc[:breakout_pos].tail(55).max())
    frame.iloc[breakout_pos, frame.columns.get_loc("open")] = prior_high + 0.20
    frame.iloc[breakout_pos, frame.columns.get_loc("high")] = prior_high + 2.00
    frame.iloc[breakout_pos, frame.columns.get_loc("low")] = prior_high + 0.10
    frame.iloc[breakout_pos, frame.columns.get_loc("close")] = prior_high + 1.50
    frame.iloc[breakout_pos, frame.columns.get_loc("volume")] = 2_000_000.0
    # Two sessions later the price loses the breakout level by a meaningful margin.
    frame.iloc[-1, frame.columns.get_loc("open")] = prior_high - 1.0
    frame.iloc[-1, frame.columns.get_loc("high")] = prior_high - 0.5
    frame.iloc[-1, frame.columns.get_loc("low")] = prior_high - 2.5
    frame.iloc[-1, frame.columns.get_loc("close")] = prior_high - 2.0
    frame.iloc[-1, frame.columns.get_loc("volume")] = 1_800_000.0
    snap = compute_daily_weekly_trader_snapshot(frame, _cfg())
    assert snap["active_breakout_level"] is not None
    assert snap["active_breakout_age_sessions"] is not None
    assert snap["failed_breakout"] is True
    assert snap["entry_state"] == "ENTRY_CONFLICT_SHADOW"
    assert "FAILED_BREAKOUT" in snap["warnings"]


def test_v2431_defers_current_calendar_day_before_local_close():
    cfg = _cfg()
    idx = pd.bdate_range("2026-08-10", periods=8)
    frame = pd.DataFrame(
        {
            "open": np.arange(8.0) + 100.0,
            "high": np.arange(8.0) + 101.0,
            "low": np.arange(8.0) + 99.0,
            "close": np.arange(8.0) + 100.5,
            "volume": np.full(8, 1_000_000.0),
        },
        index=idx,
    )
    # Last bar is 19/08/2026. At 16:00 Paris it must be deferred.
    frame.loc[pd.Timestamp("2026-08-19")] = [110.0, 111.0, 109.0, 110.5, 1_000_000.0]
    before_close = datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)
    filtered, deferred = _completed_daily_history(frame.sort_index(), cfg, now=before_close)
    assert deferred is True
    assert pd.Timestamp(filtered.index[-1]).date().isoformat() != "2026-08-19"

    after_close = datetime(2026, 8, 19, 17, 0, tzinfo=timezone.utc)
    retained, deferred_after = _completed_daily_history(frame.sort_index(), cfg, now=after_close)
    assert deferred_after is False
    assert pd.Timestamp(retained.index[-1]).date().isoformat() == "2026-08-19"


def test_v2431_governance_and_thresholds_are_pre_registered():
    cfg = _cfg()
    policy = cfg["data_policy"]
    gov = cfg["governance"]
    th = cfg["shadow_thresholds"]
    assert policy["completed_daily_bars_only"] is True
    assert policy["intraday_forbidden"] is True
    assert policy["five_minute_forbidden"] is True
    assert policy["new_market_data_downloads_required"] is False
    assert gov["decision_influence"] == 0.0
    assert gov["score_influence"] == 0.0
    assert gov["sizing_influence"] == 0.0
    assert gov["stop_loss_influence"] == 0.0
    assert gov["promotion_authority"] is False
    assert th["minimum_entry_coverage"] >= 0.80
    assert th["entry_strong_min_confirmations"] >= th["entry_ready_min_confirmations"]
    assert th["max_exit_risk_for_entry"] <= th["exit_watch"]
    assert abs(sum(cfg["entry_weights"].values()) - 1.0) < 1e-12
    assert abs(sum(cfg["exit_risk_weights"].values()) - 1.0) < 1e-12
