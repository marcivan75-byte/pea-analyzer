from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from v182.audit.pit_loader import PITDataUnavailable, load_pit_file, pit_cutoff
from v182.backtest.v21_8_1_backtest_B_v2 import compute_true_26w_pnl, detect_B_v2


def test_stop_touched_j10_is_realized_minus_nine_percent():
    idx = pd.date_range("2024-01-02", periods=20, freq="B")
    low = np.full(20, 95.0)
    low[9] = 90.0
    hist = pd.DataFrame(
        {"low": low, "high": np.full(20, 105.0), "close": np.full(20, 104.0)},
        index=idx,
    )
    pnl, hit_stop, day_stop, exit_price = compute_true_26w_pnl(100.0, hist, stop_pct=0.09)
    assert hit_stop is True
    assert day_stop == 9
    assert pnl == -0.09
    assert exit_price == 91.0


def test_b2_daily_is_active_on_next_day():
    idx = pd.date_range("2024-01-01", periods=30, freq="B")
    close = np.full(30, 100.0)
    volume = np.array([90.0 + 2.0 * (i % 11) for i in range(30)])
    close[20] = 97.0
    volume[20] = 500.0
    close[21:] = 97.5
    df = pd.DataFrame({"close": close, "volume": volume}, index=idx)
    out = detect_B_v2(df)
    assert bool(out.loc[idx[20], "B1_vol_v2"])
    assert bool(out.loc[idx[21], "B2_daily"])


def test_pit_loader_refuses_future_only_data(tmp_path: Path):
    audit = tmp_path / "outputs" / "audit"
    audit.mkdir(parents=True)
    source = audit / "sample.csv"
    pd.DataFrame(
        {
            "isin": ["FR0000000001"],
            "observed_at": ["2024-01-10T21:30:00Z"],
            "value": [1.0],
        }
    ).to_csv(source, index=False)

    # For T=2024-01-10 Paris, the latest admissible timestamp is T-1 22:00 Paris.
    with pytest.raises(PITDataUnavailable):
        load_pit_file(source, datetime(2024, 1, 10, 12, 0, tzinfo=ZoneInfo("Europe/Paris")))


def test_pit_cutoff_is_previous_day_22_paris():
    cutoff = pit_cutoff(datetime(2024, 1, 10, 9, 0, tzinfo=ZoneInfo("Europe/Paris")))
    assert cutoff == datetime(2024, 1, 9, 21, 0, tzinfo=ZoneInfo("UTC"))
