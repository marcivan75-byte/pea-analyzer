import numpy as np
import pandas as pd


def test_wave5_missing_yahoo_consensus_is_not_invented():
    from v182.reporting.waves import _rating_from_yf
    row = pd.Series({"recommendation_key_yf": np.nan, "recommendation_mean_yf": np.nan})
    assert _rating_from_yf(row) == (None, None)
