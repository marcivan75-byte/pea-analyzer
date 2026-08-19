from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.features.tct_daily_trader_v24_3 import compute_daily_weekly_trader_snapshot


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_3_0_DAILY_TRADER_SHADOW.json").read_text(encoding="utf-8"))


def _daily_history() -> pd.DataFrame:
    idx = pd.bdate_range("2026-03-02", periods=100)
    close = np.linspace(90.0, 105.0, len(idx))
    volume = np.full(len(idx), 1_000_000.0)
    frame = pd.DataFrame(
        {
            "open": close - 0.25,
            "high": close + 0.50,
            "low": close - 0.50,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )
    # Strong daily breakout with volume confirmation on the last completed day.
    frame.iloc[-1, frame.columns.get_loc("open")] = 105.4
    frame.iloc[-1, frame.columns.get_loc("high")] = 109.0
    frame.iloc[-1, frame.columns.get_loc("low")] = 105.1
    frame.iloc[-1, frame.columns.get_loc("close")] = 108.6
    frame.iloc[-1, frame.columns.get_loc("volume")] = 2_500_000.0
    return frame


def test_v243_uses_daily_and_derived_weekly_only():
    snap = compute_daily_weekly_trader_snapshot(_daily_history(), _cfg())
    assert snap["status"] == "SUCCESS_SHADOW"
    assert snap["intraday_data_used"] is False
    assert snap["new_market_data_downloads_required"] is False
    assert snap["breakout_20d"] is True
    assert snap["breakout_55d"] is True
    assert snap["daily_rvol"] > 2.0
    assert snap["weekly_close"] is not None
    assert snap["weekly_sma10"] is not None
    assert snap["previous_week_high"] is not None
    assert snap["previous_day_pivot"] is not None
    assert snap["rolling_volume_weighted_price_20d"] is not None
    assert snap["entry_score"] is not None
    assert snap["exit_risk_score"] is not None
    assert "BREAKOUT_55D" in snap["entry_reasons"]


def test_v243_is_causal_against_future_daily_bar_mutation():
    cfg = _cfg()
    raw = _daily_history()
    cut = raw.index[-2]
    first = compute_daily_weekly_trader_snapshot(raw.loc[:cut], cfg)

    mutated = raw.copy()
    mutated.loc[mutated.index > cut, ["open", "high", "low", "close", "volume"]] = [50.0, 200.0, 20.0, 180.0, 99_000_000.0]
    second = compute_daily_weekly_trader_snapshot(mutated.loc[:cut], cfg)

    comparable = [
        "entry_score",
        "exit_risk_score",
        "daily_rvol",
        "atr14",
        "ema9",
        "ema20",
        "weekly_close",
        "weekly_sma10",
        "rolling_volume_weighted_price_20d",
        "previous_day_pivot",
    ]
    for field in comparable:
        a = first[field]
        b = second[field]
        if isinstance(a, float):
            assert np.isclose(a, b, equal_nan=True)
        else:
            assert a == b


def test_v243_governance_forbids_intraday_and_production_authority():
    cfg = _cfg()
    policy = cfg["data_policy"]
    gov = cfg["governance"]
    assert policy["daily_ohlcv_only"] is True
    assert policy["weekly_derived_from_daily"] is True
    assert policy["intraday_forbidden"] is True
    assert policy["five_minute_forbidden"] is True
    assert policy["quasi_realtime_forbidden"] is True
    assert policy["new_market_data_downloads_required"] is False
    assert policy["source_cache"] == "data/cache/actions"
    assert gov["decision_influence"] == 0.0
    assert gov["score_influence"] == 0.0
    assert gov["sizing_influence"] == 0.0
    assert gov["stop_loss_influence"] == 0.0
    assert gov["ct_influence"] == 0.0
    assert gov["real_orders_enabled"] is False
    assert gov["fixed_take_profit_enabled"] is False
    assert gov["fixed_stop_loss_enabled"] is False
    assert gov["holdout_locked"] is True
    assert gov["retuning_allowed"] is False
    assert gov["promotion_authority"] is False
    assert abs(sum(cfg["entry_weights"].values()) - 1.0) < 1e-12
    assert abs(sum(cfg["exit_risk_weights"].values()) - 1.0) < 1e-12
