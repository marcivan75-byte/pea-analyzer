from __future__ import annotations

import pandas as pd

from v182.reporting.tct_sector_committee import build_context


def test_missing_timing_row_is_reported_as_data_gap_not_fabricated_signal():
    baseline=pd.DataFrame([{
        "isin":"FR0000000001","name":"No Timing","sector_yf":"Financial Services",
        "tct_baseline_score":71.0,"tct_baseline_coverage":0.80,"tct_baseline_rank":12,
        "tct_baseline_status":"BASELINE_TOP20","days_to_earnings":12,
    }])
    details,dashboard,gaps,summary=build_context(baseline,pd.DataFrame())
    row=details.iloc[0]
    assert row["timing_status"] == "TCT_TIMING_NOT_AVAILABLE"
    assert row["timing_decision"] == "NO_T1_T2"
    assert row["timing_data_gap_flag"]
    assert not row["timing_t1_flag"]
    assert not row["timing_t2_flag"]
    assert summary["t1_shadow_rows"] == 0
    assert summary["t2_shadow_rows"] == 0
    assert dashboard.iloc[0]["timing_data_gap_count"] == 1
    assert gaps.empty
