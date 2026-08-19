from __future__ import annotations

import numpy as np
import pandas as pd

from v182.reporting.tct_next_session_catalyst_run import _label_preopen_outcomes


def test_preopen_prediction_is_labeled_only_from_later_daily_close():
    ledger = pd.DataFrame(
        [
            {
                "snapshot_key": "2026-08-19|PREOPEN|FR1",
                "snapshot_generated_at_utc": "2026-08-19T06:40:00+00:00",
                "phase": "PREOPEN",
                "isin": "FR1",
                "as_of_date": "2026-08-18",
                "reference_close": 100.0,
                "direction_bias_score": 60.0,
                "movement_potential_score": 80.0,
            },
            {
                "snapshot_key": "2026-08-19|POSTMARKET|FR1",
                "snapshot_generated_at_utc": "2026-08-19T21:15:00+00:00",
                "phase": "POSTMARKET",
                "isin": "FR1",
                "as_of_date": "2026-08-19",
                "reference_close": 105.0,
                "direction_bias_score": 40.0,
                "movement_potential_score": 75.0,
            },
        ]
    )
    seed = pd.DataFrame([{"isin": "FR1", "as_of_date": "2026-08-19", "reference_close": 105.0}])
    labeled, count = _label_preopen_outcomes(ledger, seed, "2026-08-19T21:15:00+00:00")

    assert count == 1
    pre = labeled[labeled["phase"] == "PREOPEN"].iloc[0]
    post = labeled[labeled["phase"] == "POSTMARKET"].iloc[0]
    assert np.isclose(float(pre["realized_close_to_close_return_pct"]), 5.0)
    assert np.isclose(float(pre["realized_abs_return_pct"]), 5.0)
    assert float(pre["realized_direction_hit"]) == 1.0
    assert pre["outcome_as_of_date"] == "2026-08-19"
    assert pd.isna(post["realized_close_to_close_return_pct"])


def test_same_day_seed_cannot_label_preopen_forecast():
    ledger = pd.DataFrame(
        [
            {
                "phase": "PREOPEN",
                "isin": "FR1",
                "as_of_date": "2026-08-19",
                "reference_close": 100.0,
                "direction_bias_score": -50.0,
                "snapshot_generated_at_utc": "2026-08-19T06:40:00+00:00",
            }
        ]
    )
    seed = pd.DataFrame([{"isin": "FR1", "as_of_date": "2026-08-19", "reference_close": 95.0}])
    labeled, count = _label_preopen_outcomes(ledger, seed, "2026-08-19T21:15:00+00:00")
    assert count == 0
    assert pd.isna(labeled.iloc[0]["realized_close_to_close_return_pct"])
