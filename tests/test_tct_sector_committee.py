from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from v182.reporting.tct_sector_committee import build_context, earnings_context, run


def _baseline() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "isin":"A1","name":"Alpha","yahoo_ticker":"A.PA","sector_yf":"Technology","last_close":10.0,"currency":"EUR",
            "tct_baseline_score":82.0,"tct_baseline_coverage":0.85,"tct_baseline_rank":1,"tct_baseline_status":"BASELINE_TOP20",
            "tct_baseline_component_earnings":70.0,"days_to_earnings":1,"eps_revision_3m":5.0,"beat_rate":75.0,"short_interest":12.0,
        },
        {
            "isin":"A2","name":"Beta","yahoo_ticker":"B.PA","last_close":20.0,"currency":"EUR",
            "tct_baseline_score":65.0,"tct_baseline_coverage":0.70,"tct_baseline_rank":30,"tct_baseline_status":"BASELINE_RANKED",
            "days_to_earnings":4,
        },
    ])


def _timing() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "isin":"A1","decision":"T2_CONFIRM_75_SHADOW","status":"T2_CONFIRM_75_SHADOW","setup":"T2_CONFIRMATION",
            "score":80.0,"coverage_pct":100.0,"t1_t2_score_influence":0.0,"t1_t2_live_execution_allowed":False,"t2_quality_score":80.0,
        },
        {
            "isin":"A2","decision":"NO_T1_T2","status":"SHADOW_NO_SIGNAL","score":None,"coverage_pct":0.0,
            "t1_t2_score_influence":0.0,"t1_t2_live_execution_allowed":False,
        },
    ])


def test_earnings_context_boundaries_are_context_only():
    assert earnings_context(None)["earnings_bucket"] == "EARNINGS_UNKNOWN"
    assert earnings_context(1)["earnings_bucket"] == "EARNINGS_D0_1"
    assert earnings_context(1)["event_gap_risk_flag"] is True
    assert earnings_context(2)["earnings_bucket"] == "EARNINGS_D2_5"
    assert earnings_context(6)["earnings_bucket"] == "EARNINGS_D6_10"
    assert earnings_context(11)["earnings_bucket"] == "EARNINGS_D11_20"
    assert earnings_context(21)["earnings_bucket"] == "EARNINGS_D21_PLUS"


def test_sector_context_preserves_baseline_and_keeps_t1_t2_shadow_only():
    details,dashboard,gaps,summary=build_context(_baseline(),_timing())
    alpha=details[details["isin"].eq("A1")].iloc[0]
    assert alpha["sector"] == "TECHNOLOGIE"
    assert alpha["baseline_note_10"] == 8.2
    assert alpha["tct_baseline_score"] == 82.0
    assert alpha["timing_t2_flag"]
    assert alpha["t1_t2_score_influence"] == 0.0
    assert alpha["live_orders_enabled"] is False or alpha["live_orders_enabled"] == False
    assert summary["score_changes"] is False
    assert summary["new_earnings_weight_added"] is False
    assert summary["t2_shadow_rows"] == 1
    assert summary["event_gap_risk_rows"] == 1
    assert len(dashboard) == 2
    assert len(gaps) == 1
    assert gaps.iloc[0]["isin"] == "A2"


def test_nonzero_t1_t2_influence_is_rejected():
    timing=_timing(); timing.loc[0,"t1_t2_score_influence"]=0.01
    with pytest.raises(RuntimeError,match="TCT_TIMING_SCORE_INFLUENCE_NONZERO"):
        build_context(_baseline(),timing)


def test_run_writes_dashboard_details_gaps_summary_and_workbook(tmp_path:Path):
    outdir=tmp_path/"outputs"/"committee_master"; outdir.mkdir(parents=True)
    _baseline().to_csv(outdir/"TCT_BASELINE_V24_1_8.csv",sep=";",index=False,encoding="utf-8-sig")
    _timing().to_csv(outdir/"TCT_SHADOW_V24_1_7.csv",sep=";",index=False,encoding="utf-8-sig")
    result=run(tmp_path)
    assert result["status"] == "SUCCESS"
    for name in (
        "TCT_SECTOR_COMMITTEE_DETAILS.csv","TCT_SECTOR_DASHBOARD.csv","TCT_SECTOR_CLASSIFICATION_GAPS.csv",
        "TCT_SECTOR_CONTEXT_SUMMARY.json","TCT_SECTOR_COMMITTEE.xlsx",
    ):
        assert (outdir/name).exists()
