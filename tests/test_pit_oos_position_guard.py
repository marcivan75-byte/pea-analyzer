import pandas as pd

from v182.backtest.pit_oos_governed import enforce_single_position_per_isin


def test_overlapping_same_isin_signal_is_blocked_until_exit():
    trades = pd.DataFrame(
        [
            {"signal_date": "2024-07-31", "entry_date": "2024-08-01", "exit_date": "2025-03-10", "isin": "FR0011869320", "rank_on_date": 1},
            {"signal_date": "2024-08-30", "entry_date": "2024-09-02", "exit_date": "2025-04-09", "isin": "FR0011869320", "rank_on_date": 1},
            {"signal_date": "2025-04-30", "entry_date": "2025-05-02", "exit_date": "2025-06-10", "isin": "FR0011869320", "rank_on_date": 2},
        ]
    )
    filtered, blocked = enforce_single_position_per_isin(trades)
    assert len(filtered) == 2
    assert len(blocked) == 1
    assert blocked.iloc[0]["reason"] == "DUPLICATE_ISIN_WHILE_POSITION_OPEN"
    assert filtered["signal_date"].tolist() == ["2024-07-31", "2025-04-30"]
