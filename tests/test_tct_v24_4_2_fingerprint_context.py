from __future__ import annotations

import pandas as pd

from v182.reporting.tct_v24_4_2_pit_lineage import PREDICTION_FIELDS, prediction_fingerprint


def _row() -> pd.Series:
    return pd.Series(
        {
            "version": "TCT_V24.4.2_NEXT_SESSION_CATALYST_CYCLE_SHADOW",
            "phase": "PREOPEN",
            "isin": "FR0000000001",
            "yahoo_ticker": "TEST.PA",
            "as_of_date": "2026-08-20",
            "reference_close": 100.0,
            "sector_yf": "Industrials",
            "global_risk_on_score": 55.0,
            "global_vix_return_pct": -2.0,
            "global_eurostoxx50_return_pct": 0.8,
            "global_cac40_return_pct": 0.6,
            "global_dax_return_pct": 0.7,
            "snapshot_generated_at_utc": "2026-08-21T06:40:00+00:00",
            "snapshot_key": "2026-08-21|PREOPEN|FR0000000001",
        }
    )


def test_validation_context_fields_are_in_prediction_fingerprint_contract():
    for field in (
        "sector_yf",
        "global_vix_return_pct",
        "global_eurostoxx50_return_pct",
        "global_cac40_return_pct",
        "global_dax_return_pct",
    ):
        assert field in PREDICTION_FIELDS


def test_sector_or_vix_context_mutation_invalidates_prediction_fingerprint():
    row = _row()
    original = prediction_fingerprint(row)
    changed_sector = row.copy()
    changed_sector["sector_yf"] = "Technology"
    changed_vix = row.copy()
    changed_vix["global_vix_return_pct"] = 4.0
    assert prediction_fingerprint(changed_sector) != original
    assert prediction_fingerprint(changed_vix) != original
