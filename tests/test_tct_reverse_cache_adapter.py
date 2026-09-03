import numpy as np
import pandas as pd

from v182.backtest.tct_reverse_cache_adapter import cache_frame_to_long


def test_cache_adapter_reads_ticker_field_multiindex():
    dates = pd.bdate_range("2026-01-02", periods=3)
    columns = pd.MultiIndex.from_product([["AAA.PA", "BBB.PA"], ["Open", "High", "Low", "Close", "Volume"]])
    values = np.array([
        [10, 11, 9, 10.5, 1000, 20, 21, 19, 20.5, 2000],
        [10.5, 12, 10, 11.5, 1500, 20.5, 22, 20, 21.5, 2500],
        [11.5, 13, 11, 12.5, 1800, 21.5, 23, 21, 22.5, 2800],
    ])
    frame = pd.DataFrame(values, index=dates, columns=columns)
    out = cache_frame_to_long(frame)
    assert len(out) == 6
    assert set(out["instrument_id"]) == {"AAA.PA", "BBB.PA"}
    assert {"open", "high", "low", "close", "volume"}.issubset(out.columns)
    assert out.loc[out["instrument_id"] == "AAA.PA", "close"].iloc[-1] == 12.5


def test_cache_adapter_reads_field_ticker_multiindex():
    dates = pd.bdate_range("2026-01-02", periods=2)
    columns = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], ["AAA.PA"]])
    values = np.array([
        [10, 11, 9, 10.5, 1000],
        [10.5, 12, 10, 11.5, 1500],
    ])
    frame = pd.DataFrame(values, index=dates, columns=columns)
    out = cache_frame_to_long(frame)
    assert len(out) == 2
    assert set(out["instrument_id"]) == {"AAA.PA"}
    assert out["close"].tolist() == [10.5, 11.5]
