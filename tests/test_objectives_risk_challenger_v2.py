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
        "ranking_weights": {"selection": 0.55, "reward_risk": 0.30, "reliability": 0.15},
        "reward_risk_score_cap": 5.0,
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
