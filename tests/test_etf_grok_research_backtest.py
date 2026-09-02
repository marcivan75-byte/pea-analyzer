import pandas as pd
from pathlib import Path

from v182.backtest.etf_grok_research_backtest import _next_row, _simulate_exit, _stats


def test_next_session_entry_is_strictly_after_signal():
    idx = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    f = pd.DataFrame({"Close": [100.0, 101.0, 102.0]}, index=idx)
    d, px = _next_row(f, pd.Timestamp("2026-01-02"))
    assert d == pd.Timestamp("2026-01-05")
    assert px == 101.0


def test_replay_target_exit_uses_close_and_costs_are_separate():
    idx = pd.bdate_range("2026-01-02", periods=4)
    f = pd.DataFrame({"Close": [100.0, 102.0, 104.0, 105.0]}, index=idx)
    cfg = {"exit_policy": {"target_return": 0.04, "hard_stop_return": -0.18, "max_holding_sessions": 168}}
    d, px, hold, reason = _simulate_exit(f, idx[0], 100.0, cfg)
    assert reason == "TARGET_CLOSE"
    assert px == 104.0
    assert hold == 2


def test_stats_profit_factor_and_win_rate():
    trades = pd.DataFrame({"net_return": [0.05, 0.03, -0.02]})
    s = _stats(trades)
    assert s["trades"] == 3
    assert s["wins"] == 2
    assert abs(s["win_rate"] - 2 / 3) < 1e-12
    assert abs(s["profit_factor"] - 4.0) < 1e-12
