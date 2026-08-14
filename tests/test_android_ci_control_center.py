from pathlib import Path
import json
import pandas as pd

from v182.reporting.android_ci_control_center import build_markdown, run


def test_android_control_center_contains_mobile_sections_and_market_overlays(tmp_path: Path):
    (tmp_path / "outputs" / "unified").mkdir(parents=True)
    (tmp_path / "outputs" / "committee_master").mkdir(parents=True)
    (tmp_path / "outputs" / "audit").mkdir(parents=True)
    (tmp_path / "outputs" / "etf_mt_v2081").mkdir(parents=True)
    (tmp_path / "outputs" / "gold_v1_1").mkdir(parents=True)

    (tmp_path / "outputs" / "unified" / "UNIFIED_SUMMARY_LATEST.json").write_text(json.dumps({
        "status":"SUCCESS",
        "generated_at_utc":"2026-08-14T09:00:00Z",
        "steps":{"criteria_governance":{"status":"SUCCESS"},"committee":{"status":"SUCCESS"}},
    }), encoding="utf-8")
    (tmp_path / "outputs" / "committee_master" / "SUMMARY.json").write_text(json.dumps({
        "action_52w_rotation_overlay":{"positive_52w_bonus_rows":4,"near_high_malus_rows":3},
        "tct_exact_timing":{"status":"SUCCESS","t1_detected_raw":7,"t2_confirmed":2},
        "canonical_actions":{"canonical_rows":1829},
    }), encoding="utf-8")
    (tmp_path / "outputs" / "audit" / "CRITERIA_STUDY_GOVERNANCE.json").write_text(json.dumps({"status":"PASS"}), encoding="utf-8")
    (tmp_path / "outputs" / "audit" / "PYTHON_STATIC_AUDIT.json").write_text(json.dumps({"high":0,"medium":0}), encoding="utf-8")
    (tmp_path / "outputs" / "etf_mt_v2081" / "V20.8.1_ETF_MT_SUMMARY.json").write_text(json.dumps({"version":"20.8.1","scorable_etfs":80,"selected":["A","B"]}), encoding="utf-8")
    (tmp_path / "outputs" / "gold_v1_1" / "GOLD_V1_1_DECISION.json").write_text(json.dumps({"decision":"WATCH","score":72}), encoding="utf-8")

    pd.DataFrame([
        {"asset_class":"ACTION","horizon":"MT","isin":"FR1","name":"Action A","score":84,"decision":"BUY_CANDIDATE"},
        {"asset_class":"ETF","horizon":"MT","isin":"FR2","name":"ETF B","score":83,"decision":"WATCH"},
    ]).to_csv(tmp_path / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    pd.DataFrame([
        {"sector":"Technology","sector_rotation_score":81.2,"recovery_gate":True},
        {"sector":"Industrials","sector_rotation_score":68.1,"recovery_gate":True},
    ]).to_csv(tmp_path / "outputs" / "V21_3_SECTOR_ROTATION.csv", sep=";", index=False, encoding="utf-8-sig")

    text = build_markdown(tmp_path)
    assert "PEA ANALYZER — CONTROL CENTER ANDROID" in text
    assert "COMITÉ D’INVESTISSEMENT" in text
    assert "PLUS HAUT 52 SEMAINES" in text
    assert "≤2% du plus haut = -4 pts" in text
    assert "ROTATION SECTORIELLE" in text
    assert "Technology: 81.2/100" in text
    assert "ETF MT" in text
    assert "TCT" in text
    assert "OR" in text
    assert "DATA QUALITY" in text
    assert "BACKTEST PIT/OOS EXCEPTIONNEL" in text
    assert "GITHUB STATUS / INCIDENTS" in text

    result = run(tmp_path)
    assert result["status"] == "SUCCESS"
    assert (tmp_path / "outputs" / "mobile" / "ANDROID_CI_CONTROL_CENTER.md").exists()
