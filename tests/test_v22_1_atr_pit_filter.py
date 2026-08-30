import numpy as np
import pandas as pd

from tools.v22_1_data.filter_atr_pit import filter_valid_atr


def test_filter_valid_atr_blocks_only_invalid_rows():
    frame = pd.DataFrame(
        {
            "ticker": ["AAA.PA", "BBB.PA", "CCC.PA", "DDD.PA", "EEE.PA"],
            "as_of_date": pd.to_datetime(["2024-01-05"] * 5),
            "atr_14_pct": [0.02, np.nan, 0.0, -0.01, "bad"],
        }
    )

    filtered, report = filter_valid_atr(frame)

    assert list(filtered["ticker"]) == ["AAA.PA"]
    assert report["rows_input"] == 5
    assert report["rows_valid_atr"] == 1
    assert report["rows_blocked_invalid_atr"] == 4
    assert report["valid_atr_coverage"] == 0.2
    assert report["governance"]["invalid_atr_imputed"] is False


def test_filter_valid_atr_reports_yearly_coverage():
    frame = pd.DataFrame(
        {
            "ticker": ["AAA.PA", "BBB.PA", "CCC.PA"],
            "as_of_date": pd.to_datetime(["2023-01-06", "2023-01-13", "2024-01-05"]),
            "atr_14_pct": [0.02, np.nan, 0.03],
        }
    )

    _, report = filter_valid_atr(frame)

    assert report["by_year"]["2023"]["coverage"] == 0.5
    assert report["by_year"]["2024"]["coverage"] == 1.0
