from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting.committee_executive_views import (
    build_action_priority,
    build_etf_top30,
    build_tct_views,
    run,
)


def _decisions() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "asset_class":"ACTION","horizon":"CT","isin":"A1","name":"Alpha","sector":"TECHNOLOGIE",
            "score":81.0,"coverage_pct":88.0,"status":"SCORABLE","decision":"BUY_CANDIDATE",
            "score_source":"V21.0_REFERENCE","backtest_attribution":"",
            "action_reference_score":81.0,"action_reference_decision":"BUY_CANDIDATE",
            "action_challenger_score":86.0,"action_challenger_decision":"BUY_CANDIDATE",
            "action_score_delta_challenger_vs_reference":5.0,
        },
        {
            "asset_class":"ACTION","horizon":"CT","isin":"A2","name":"Beta","sector":"INDUSTRIE",
            "score":70.0,"coverage_pct":76.0,"status":"SCORABLE","decision":"WATCH",
            "score_source":"V21.0_REFERENCE","backtest_attribution":"",
            "action_reference_score":70.0,"action_reference_decision":"WATCH",
            "action_challenger_score":74.0,"action_challenger_decision":"WATCH",
            "action_score_delta_challenger_vs_reference":4.0,
        },
        {
            "asset_class":"ETF","horizon":"MT","isin":"E1","name":"ETF One","sector":"ETF MULTISECTORIEL / PAYS",
            "score":91.0,"coverage_pct":100.0,"status":"SCORABLE","decision":"BUY_CANDIDATE",
            "score_source":"V20.8.1_DYNAMIC_38_CORE","backtest_attribution":"Historical OOS validation 2021-2023: 90.91% for the 38 dynamic PIT core only.",
        },
        {
            "asset_class":"ETF","horizon":"CT","isin":"E1","name":"ETF One","sector":"ETF MULTISECTORIEL / PAYS",
            "score":72.0,"coverage_pct":90.0,"status":"SCORABLE","decision":"WATCH",
            "score_source":"ETF_CT_REFERENCE","backtest_attribution":"",
        },
        {
            "asset_class":"ETF","horizon":"MT","isin":"E2","name":"ETF Two","sector":"ETF MULTISECTORIEL / PAYS",
            "score":80.0,"coverage_pct":98.0,"status":"SCORABLE","decision":"BUY_CANDIDATE",
            "score_source":"V20.8.1_DYNAMIC_38_CORE","backtest_attribution":"Historical OOS validation 2021-2023: 90.91% for the 38 dynamic PIT core only.",
        },
    ])


def _tct_details() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "isin":"A1","name":"Alpha","sector":"TECHNOLOGIE","last_price":10.0,"price_currency":"EUR",
            "tct_baseline_rank":1,"tct_baseline_score":82.0,"tct_baseline_coverage_pct":85.0,
            "tct_baseline_status":"BASELINE_TOP20","timing_setup":"T2_CONFIRMATION",
            "timing_decision":"T2_CONFIRM_75_SHADOW","timing_status":"T2_CONFIRM_75_SHADOW",
            "timing_quality_score":80.0,"t2_quality_score":80.0,"timing_t1_flag":False,"timing_t2_flag":True,
            "timing_data_gap_flag":False,"days_to_earnings":1,"earnings_bucket":"EARNINGS_D0_1",
            "event_risk_level":"HIGH","event_gap_risk_flag":True,"eps_revision_3m":5.0,"short_interest_pct":0.7,
            "sector_classification_quality":"ATTRIBUTED_OR_PROVIDER_FIELD","sector_gap_flag":False,
            "reporting_role":"REPORTING_CONTEXT_ONLY","t1_t2_score_influence":0.0,"live_orders_enabled":False,
        },
        {
            "isin":"A2","name":"Beta","sector":"INDUSTRIE","last_price":20.0,"price_currency":"EUR",
            "tct_baseline_rank":2,"tct_baseline_score":78.0,"tct_baseline_coverage_pct":80.0,
            "tct_baseline_status":"BASELINE_TOP20","timing_setup":"T1_SETUP",
            "timing_decision":"T1_SETUP_70_SHADOW","timing_status":"T1_SETUP_70_SHADOW",
            "timing_quality_score":72.0,"t1_quality_score":72.0,"timing_t1_flag":True,"timing_t2_flag":False,
            "timing_data_gap_flag":False,"days_to_earnings":4,"earnings_bucket":"EARNINGS_D2_5",
            "event_risk_level":"ELEVATED","event_gap_risk_flag":False,
            "sector_classification_quality":"ATTRIBUTED_OR_PROVIDER_FIELD","sector_gap_flag":False,
            "reporting_role":"REPORTING_CONTEXT_ONLY","t1_t2_score_influence":0.0,"live_orders_enabled":False,
        },
    ])


def _baseline() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "isin":"A1","market_cap":2_500_000_000,"rvol20":2.1,"earnings_date_finnhub":"2026-08-14",
            "earnings_hour_finnhub":"amc","eps_estimate_analysts_finnhub":12,"eps_estimate_dispersion_pct_finnhub":8.0,
            "amf_public_short_holders_count":2,"amf_public_short_max_holder_pct":0.7,"amf_short_data_as_of":"2026-08-13",
        },
        {"isin":"A2","market_cap":500_000_000,"rvol20":1.4},
    ])


def _dashboard() -> pd.DataFrame:
    return pd.DataFrame([
        {"sector":"TECHNOLOGIE","action_count":1,"baseline_top20_count":1},
        {"sector":"INDUSTRIE","action_count":1,"baseline_top20_count":1},
    ])


def test_action_priority_is_presentation_only_and_preserves_scores():
    result = build_action_priority(_decisions(), top_n_per_horizon=30)
    alpha = result[result["isin"].eq("A1")].iloc[0]
    assert alpha["score"] == 81.0
    assert alpha["action_reference_score"] == 81.0
    assert alpha["action_challenger_score"] == 86.0
    assert "probability" not in " ".join(result.columns).lower()
    assert "expectancy" not in " ".join(result.columns).lower()
    assert "proba" not in " ".join(result.columns).lower()
    assert "esperance" not in " ".join(result.columns).lower()


def test_etf_top30_exposes_existing_horizons_and_preserves_mt_reference():
    result = build_etf_top30(_decisions())
    e1 = result[result["isin"].eq("E1")].iloc[0]
    assert e1["score_mt"] == 91.0
    assert e1["score_ct"] == 72.0
    assert e1["decision_mt"] == "BUY_CANDIDATE"
    assert e1["mt_reference_validated_flag"]
    assert e1["selected_horizon_count"] == 1


def test_tct_views_use_baseline_rank_and_shadow_timing_only():
    views = build_tct_views(_tct_details(), _dashboard(), _baseline())
    assert list(views["top50"]["isin"]) == ["A1", "A2"]
    assert list(views["t2"]["isin"]) == ["A1"]
    assert list(views["t1"]["isin"]) == ["A2"]
    assert set(views["earnings_d0_5"]["isin"]) == {"A1", "A2"}
    assert list(views["event_risk"]["isin"]) == ["A1"]
    alpha = views["top50"][views["top50"]["isin"].eq("A1")].iloc[0]
    assert alpha["market_cap_native_m"] == 2500.0
    assert alpha["relative_volume_20d"] == 2.1
    assert alpha["t1_t2_score_influence"] == 0.0
    assert not bool(alpha["live_orders_enabled"])
    tech = views["sectors"][views["sectors"]["sector"].eq("TECHNOLOGIE")].iloc[0]
    assert tech["top_3_baseline"] == "Alpha"
    assert tech["top_3_timing_shadow"] == "Alpha"


def test_missing_optional_timing_flags_do_not_crash():
    details = _tct_details().drop(columns=["timing_t1_flag", "timing_t2_flag", "event_gap_risk_flag"])
    views = build_tct_views(details, _dashboard(), _baseline())
    assert views["t1"].empty
    assert views["t2"].empty
    assert views["event_risk"].empty
    assert len(views["top50"]) == 2


def test_run_writes_compact_executive_outputs(tmp_path: Path):
    outdir = tmp_path / "outputs" / "committee_master"
    outdir.mkdir(parents=True)
    _decisions().to_csv(outdir / "COMMITTEE_DECISIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    _tct_details().to_csv(outdir / "TCT_SECTOR_COMMITTEE_DETAILS.csv", sep=";", index=False, encoding="utf-8-sig")
    _dashboard().to_csv(outdir / "TCT_SECTOR_DASHBOARD.csv", sep=";", index=False, encoding="utf-8-sig")
    _baseline().to_csv(outdir / "TCT_BASELINE_V24_1_8.csv", sep=";", index=False, encoding="utf-8-sig")
    result = run(tmp_path)
    assert result["status"] == "SUCCESS"
    assert result["score_changes"] is False
    assert result["new_composite_opportunity_score"] is False
    assert result["fixed_probability_or_expectancy_added"] is False
    assert result["t1_t2_score_influence"] == 0.0
    assert result["live_orders_enabled"] is False
    for name in (
        "ACTION_COMMITTEE_PRIORITY_BY_HORIZON.csv",
        "ETF_COMMITTEE_TOP30.csv",
        "TCT_COMMITTEE_TOP50.csv",
        "TCT_EARNINGS_D0_5_CONTEXT.csv",
        "COMMITTEE_DATA_QUALITY.csv",
        "COMMITTEE_EXECUTIVE_VIEWS_SUMMARY.json",
        "COMMITTEE_EXECUTIVE_VIEWS.xlsx",
    ):
        assert (outdir / name).exists()
