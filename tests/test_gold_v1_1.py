from pathlib import Path
import json
import pandas as pd

from v182.decision.gold_v1_1 import (
    _score_horizon, _decision_tactical, _decision_strategic,
    _synthetic_xau_eur, _load_manual_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "GOLD_V1_1_102_CRITERIA.json").read_text(encoding="utf-8"))


def test_gold_registry_is_exactly_102_and_top_level_weights_are_preserved():
    assert CFG["governance"]["criteria_count"] == 102
    assert len(CFG["criteria"]) == 102
    assert len({c["name"] for c in CFG["criteria"]}) == 102
    assert len(CFG["horizons"]["TACTICAL_2_12W"]["block_weights"]) == 11
    assert abs(sum(CFG["horizons"]["TACTICAL_2_12W"]["block_weights"].values()) - 1.0) < 1e-12
    assert abs(sum(CFG["horizons"]["STRATEGIC_6_24M"]["block_weights"].values()) - 1.0) < 1e-12
    assert CFG["horizons"]["TACTICAL_2_12W"]["source_family_weights"] == {"technical":0.45,"macro":0.25,"flows":0.20,"positioning":0.10}
    assert CFG["horizons"]["STRATEGIC_6_24M"]["source_family_weights"] == {"real_rates_usd":0.25,"central_banks":0.20,"gold_etf_flows":0.20,"cftc":0.15,"stress_geopolitics":0.10,"technical":0.10}
    assert CFG["governance"]["t1_t2_forbidden"] is True
    assert CFG["live_orders_enabled"] is False


def test_missing_values_are_not_neutral_imputed_and_coverage_blocks():
    empty = _score_horizon(CFG, {}, "TACTICAL_2_12W")
    assert empty["score"] is None
    assert empty["coverage_pct"] == 0.0
    assert empty["status"] == "BLOCK_DATA"
    one = {CFG["criteria"][0]["name"]: 100.0}
    partial = _score_horizon(CFG, one, "TACTICAL_2_12W")
    assert partial["coverage_pct"] < 70.0
    assert partial["status"] == "BLOCK_DATA"
    assert partial["score"] == 100.0


def test_all_observed_scores_preserve_weighted_mean():
    values = {c["name"]: 80.0 for c in CFG["criteria"]}
    tactical = _score_horizon(CFG, values, "TACTICAL_2_12W")
    strategic = _score_horizon(CFG, values, "STRATEGIC_6_24M")
    assert tactical["coverage_pct"] == 100.0
    assert strategic["coverage_pct"] == 100.0
    assert abs(tactical["score"] - 80.0) < 1e-6
    assert abs(strategic["score"] - 80.0) < 1e-6


def test_missing_central_bank_block_does_not_destroy_referential():
    values = {c["name"]: 70.0 for c in CFG["criteria"] if c["block"] != "central_bank_physical"}
    tactical = _score_horizon(CFG, values, "TACTICAL_2_12W")
    strategic = _score_horizon(CFG, values, "STRATEGIC_6_24M")
    assert tactical["coverage_pct"] == 95.0
    assert strategic["coverage_pct"] == 80.0
    assert tactical["status"] == "SCORABLE"
    assert strategic["status"] == "SCORABLE"


def test_qds_below_70_can_never_emit_tactical_buy():
    scored = {"status":"SCORABLE","score":75.0,"coverage_pct":90.0}
    decision, reasons = _decision_tactical(CFG, scored, 69.9, 80.0, False)
    assert decision == "SHADOW_WATCH_QDS"
    assert "QDS_LT_70_NO_BUY_REINFORCE" in reasons
    decision, _ = _decision_tactical(CFG, scored, 80.0, 69.9, False)
    assert decision == "ABSTAIN_DATA_TRUST"


def test_strategic_gate_requires_coverage_and_data_trust():
    scored = {"status":"BLOCK_DATA","score":75.0,"coverage_pct":60.0}
    assert _decision_strategic(CFG, scored, 90.0)[0] == "ABSTAIN_COVERAGE"
    scored = {"status":"SCORABLE","score":75.0,"coverage_pct":80.0}
    assert _decision_strategic(CFG, scored, 69.0)[0] == "ABSTAIN_DATA_TRUST"
    assert _decision_strategic(CFG, scored, 90.0)[0] == "SHADOW_STRATEGIC_FAVORABLE"


def test_xau_eur_is_derived_from_gold_usd_proxy_divided_by_eurusd():
    idx = pd.bdate_range("2026-01-01", periods=3)
    gold = pd.DataFrame({"open":[2000,2010,2020],"high":[2010,2020,2030],"low":[1990,2000,2010],"close":[2005,2015,2025]}, index=idx)
    fx = pd.DataFrame({"close":[1.10,1.11,1.12]}, index=idx)
    out = _synthetic_xau_eur({"gold_usd_proxy":gold,"eurusd":fx})
    assert abs(out.loc[idx[-1],"close"] - 2025/1.12) < 1e-9


def test_official_snapshot_requires_date_and_source_and_never_fills_missing(tmp_path):
    cfg = json.loads(json.dumps(CFG)); cfg["wgc"]["manual_snapshot_path"] = "inputs/snapshot.json"
    p = tmp_path / "inputs"; p.mkdir()
    (p/"snapshot.json").write_text(json.dumps({"criterion_scores":{"central_bank_net_purchases_latest":80}}))
    values, status = _load_manual_snapshot(tmp_path, cfg)
    assert values == {}
    assert status["status"] == "REJECTED"
    (p/"snapshot.json").write_text(json.dumps({"as_of":"2026-08-01","source_url":"https://example.invalid/attributed-source","criterion_scores":{"central_bank_net_purchases_latest":80,"central_bank_net_purchases_trend":None}}))
    values, status = _load_manual_snapshot(tmp_path, cfg)
    assert values == {"central_bank_net_purchases_latest":80.0}
    assert "central_bank_net_purchases_trend" not in values
    assert status["status"] == "OK"
