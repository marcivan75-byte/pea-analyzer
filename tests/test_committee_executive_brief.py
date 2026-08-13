from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting.committee_executive_brief import build_html, run


def _write_inputs(outdir: Path) -> None:
    pd.DataFrame([
        {"horizon":"CT","committee_rank":1,"isin":"A1","name":"Alpha","sector":"TECHNOLOGIE","score":81.0,"coverage_pct":88.0,"decision":"BUY_CANDIDATE"},
        {"horizon":"MT","committee_rank":1,"isin":"A2","name":"Beta","sector":"INDUSTRIE","score":79.0,"coverage_pct":90.0,"decision":"BUY_CANDIDATE"},
    ]).to_csv(outdir/"ACTION_COMMITTEE_PRIORITY_BY_HORIZON.csv",sep=";",index=False,encoding="utf-8-sig")
    pd.DataFrame([
        {"isin":"E1","name":"ETF One","rank_mt":1,"score_mt":91.0,"decision_mt":"BUY_CANDIDATE","score_ct":72.0,"decision_ct":"WATCH","selected_horizon_count":1},
    ]).to_csv(outdir/"ETF_COMMITTEE_TOP30.csv",sep=";",index=False,encoding="utf-8-sig")
    pd.DataFrame([
        {"isin":"A1","name":"Alpha","sector":"TECHNOLOGIE","tct_baseline_rank":1,"tct_baseline_score":82.0,"timing_decision":"T2_CONFIRM_75_SHADOW","earnings_bucket":"EARNINGS_D0_1","event_risk_level":"HIGH"},
    ]).to_csv(outdir/"TCT_COMMITTEE_TOP50.csv",sep=";",index=False,encoding="utf-8-sig")
    pd.DataFrame([
        {"sector":"TECHNOLOGIE","action_count":10,"baseline_top20_count":3,"t1_shadow_count":2,"t2_shadow_count":1,"earnings_d0_1_count":1,"earnings_d2_5_count":2,"mean_baseline_score":68.0,"top_3_baseline":"Alpha | Gamma | Delta"},
    ]).to_csv(outdir/"TCT_SECTOR_DASHBOARD.csv",sep=";",index=False,encoding="utf-8-sig")
    pd.DataFrame([
        {"scope":"ACTION:CT","rows":1829,"mean_coverage_pct":82.0,"min_coverage_pct":70.0,"blocked_rows":0,"data_gap_rows":0},
    ]).to_csv(outdir/"COMMITTEE_DATA_QUALITY.csv",sep=";",index=False,encoding="utf-8-sig")


def test_mobile_brief_uses_current_outputs_without_old_trade_claims(tmp_path: Path):
    outdir=tmp_path/"outputs"/"committee_master"; outdir.mkdir(parents=True)
    _write_inputs(outdir)
    html=build_html(outdir)
    assert "Alpha" in html
    assert "ETF One" in html
    assert "T2_CONFIRM_75_SHADOW" in html
    assert "influence score = 0" in html
    assert "Aucun ordre live" in html
    assert "82%" not in html
    assert "+28%" not in html
    assert "URGENT" not in html
    assert "acheter 50%" not in html.lower()


def test_run_writes_self_contained_html_and_summary(tmp_path: Path):
    outdir=tmp_path/"outputs"/"committee_master"; outdir.mkdir(parents=True)
    _write_inputs(outdir)
    result=run(tmp_path)
    assert result["status"]=="SUCCESS"
    assert result["score_changes"] is False
    assert result["fixed_probability_or_expectancy_added"] is False
    assert result["t1_t2_score_influence"]==0.0
    assert result["live_orders_enabled"] is False
    html_path=outdir/"COMMITTEE_EXECUTIVE_BRIEF.html"
    assert html_path.exists()
    assert (outdir/"COMMITTEE_EXECUTIVE_BRIEF_SUMMARY.json").exists()
    text=html_path.read_text(encoding="utf-8")
    assert "<style>" in text
    assert "<meta name=\"viewport\"" in text
    assert "https://" not in text
