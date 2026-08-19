import pandas as pd
import pytest

from v182.decision.tct_intraday_shadow_v24_2 import _post_entry_outcomes


def test_post_entry_labels_exclude_entry_bar_high_and_low():
    session = pd.DataFrame(
        {
            "open": [99.0, 100.0, 101.0],
            "high": [150.0, 102.0, 103.0],
            "low": [50.0, 99.0, 98.0],
            "close": [100.0, 101.0, 102.0],
            "volume": [1000.0, 1000.0, 1000.0],
        },
        index=pd.date_range("2026-08-19 10:00", periods=3, freq="5min"),
    )

    mfe, mae, close_return = _post_entry_outcomes(session, 0, 100.0)

    assert mfe == pytest.approx(0.03)
    assert mae == pytest.approx(-0.02)
    assert close_return == pytest.approx(0.02)


def test_last_bar_entry_has_no_post_entry_outcome_window():
    session = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1000.0, 1000.0],
        },
        index=pd.date_range("2026-08-19 16:50", periods=2, freq="5min"),
    )

    assert _post_entry_outcomes(session, 1, 101.5) == (None, None, None)
