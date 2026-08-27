import json
import pandas as pd

from v182.reporting import objectives_risk_challenger_v2 as challenger


def test_numeric_sources_fall_back_row_by_row():
    frame = pd.DataFrame({"HYPER_SCORE": [80.0, None], "score": [70.0, 90.0]})
    assert challenger._num(frame, "HYPER_SCORE", "score").tolist() == [80.0, 90.0]


def test_challenger_is_shadow_and_does_not_promote_risk_off(tmp_path):
    config = {
        "version": "TEST",
        "action_reward_risk_gate": {"TCT": 2.0, "CT": 2.5, "MT": 3.0},
        "minimum_reliability": 65.0,
        "ranking_formula_version": "TEST_FORMULA",
        "ranking_weights": {"selection": 0.50, "reward_risk": 0.30, "reliability": 0.20},
        "reward_risk_mapping": {"target_ratio": 2.0, "target_score": 70.0, "cap_ratio": 4.0, "cap_score": 100.0},
        "labels": {"ready_minimum_rr": 2.0, "ready_minimum_confidence": 65.0, "watch_priority_minimum_rr": 1.5, "watch_priority_minimum_confidence": 55.0, "watch_minimum_confidence": 45.0},
        "risk_soft_multiplier": {"GREEN": 1.0, "AMBER": 0.85, "ORANGE": 0.70, "MISSING": 0.55, "RED": 0.40},
        "buy_candidate_minimum_confidence": 66.0,
        "entry_confidence_challenger": {"RISK_ON": 60.0, "NEUTRAL": 62.0, "RISK_OFF": 70.0},
        "downside_risk_challenger": {"weight": 0.10},
        "portfolio_budget": {},
        "promotion": {"automatic": False},
    }
    path = tmp_path / challenger.CONFIG
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    source = pd.DataFrame([{
        "isin": "A", "asset_class": "ACTION", "horizon": "CT", "score": 80,
        "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY": 3.0, "SIM_RELIABILITY": 70,
        "CI_CONFIDENCE_SCORE_0_100": 69, "CI_MARKET_ORIENTATION_EUROPE": "RISK_OFF",
        "SIM_STATUS": "WAIT", "risk_downside_beta_252d": 0.9,
        "SIM_CENTRAL_POTENTIAL_PCT_FROM_CURRENT": 10.0,
    }])
    input_path = tmp_path / challenger.INPUT
    input_path.parent.mkdir(parents=True)
    source.to_csv(input_path, sep=";", index=False)
    payload = challenger.run(tmp_path)
    result = pd.read_csv(tmp_path / challenger.OUTPUT, sep=";")
    assert payload["reference_modified"] is False
    assert result.iloc[0]["CHALLENGER_ENTRY_STATE"] == "WAIT"
    assert bool(result.iloc[0]["CHALLENGER_RR_GATE"]) is True
    assert bool(result.iloc[0]["CHALLENGER_REAL_ORDER_ALLOWED"]) is False
    assert result.iloc[0]["OR_FORMULA_VERSION"] == "TEST_FORMULA"
    assert result.iloc[0]["OR_HEBDO_LABEL"] == "OR_WATCH"
    assert result.iloc[0]["OR_RISK_VERDICT"] == "GREEN"
    assert result.iloc[0]["OR_COMPOSITE_SHADOW"] == 79.3


def test_missing_risk_is_soft_penalized_and_never_changes_reference(tmp_path):
    config = {
        "version": "TEST", "action_reward_risk_gate": {"TCT": 2.0, "CT": 2.5, "MT": 3.0},
        "minimum_reliability": 65.0, "ranking_formula_version": "TEST_FORMULA",
        "ranking_weights": {"selection": 0.50, "reward_risk": 0.30, "reliability": 0.20},
        "reward_risk_mapping": {"target_ratio": 2.0, "target_score": 70.0, "cap_ratio": 4.0, "cap_score": 100.0},
        "labels": {"ready_minimum_rr": 2.0, "ready_minimum_confidence": 65.0, "watch_priority_minimum_rr": 1.5, "watch_priority_minimum_confidence": 55.0, "watch_minimum_confidence": 45.0},
        "risk_soft_multiplier": {"GREEN": 1.0, "AMBER": 0.85, "ORANGE": 0.70, "MISSING": 0.55, "RED": 0.40},
        "buy_candidate_minimum_confidence": 66.0,
        "entry_confidence_challenger": {"RISK_ON": 60.0, "NEUTRAL": 62.0, "RISK_OFF": 70.0},
        "downside_risk_challenger": {"weight": 0.10}, "portfolio_budget": {},
        "promotion": {"automatic": False},
    }
    path = tmp_path / challenger.CONFIG
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    source = pd.DataFrame([{
        "isin": "ETF", "asset_class": "ETF", "horizon": "MT", "score": 80,
        "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY": 3.0, "SIM_RELIABILITY": 70,
        "CI_CONFIDENCE_SCORE_0_100": 70, "CI_MARKET_ORIENTATION_EUROPE": "NEUTRAL",
        "SIM_STATUS": "READY_FOR_REVIEW", "SIM_CENTRAL_POTENTIAL_PCT_FROM_CURRENT": 10.0,
    }])
    input_path = tmp_path / challenger.INPUT
    input_path.parent.mkdir(parents=True)
    source.to_csv(input_path, sep=";", index=False)
    challenger.run(tmp_path)
    result = pd.read_csv(tmp_path / challenger.OUTPUT, sep=";").iloc[0]
    assert result["OR_RISK_VERDICT"] == "MISSING"
    assert result["OR_RISK_SOFT_MULT"] == 0.55
    assert result["OR_COMPOSITE_SHADOW"] == 43.72
    assert result["OR_HEBDO_LABEL"] == "OR_WATCH"
    assert result["OR_DATA_CONTRACT_STATUS"] == "INCOMPLETE_FAIL_CLOSED"
    assert bool(result["CHALLENGER_REAL_ORDER_ALLOWED"]) is False


def test_reward_risk_mapping_hits_documented_anchors():
    rr = pd.Series([0.0, 2.0, 4.0, 8.0])
    mapping = {"target_ratio": 2.0, "target_score": 70.0, "cap_ratio": 4.0, "cap_score": 100.0}
    assert challenger._reward_risk_score(rr, mapping).tolist() == [0.0, 70.0, 100.0, 100.0]
