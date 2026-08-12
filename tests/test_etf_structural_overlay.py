import pandas as pd

from v182.decision.etf_structural_overlay import (
    morningstar_stars, risk_level_7, apply_etf_structural_overlay,
)


def _registry():
    return {
        "horizons":{
            "CT":{"buy_threshold":77,"watch_threshold":70,"review_threshold":60},
            "MT":{"selection_threshold":82,"watch_threshold":70,"review_threshold":60},
            "LT":{"buy_threshold":77,"watch_threshold":70,"review_threshold":60},
            "SHORT":{"short_candidate_threshold":77,"watch_threshold":70},
        },
        "bonus_malus":{
            "morningstar_bonus":{"5":5,"4":3,"3":1,"2":0,"1":0,"unrated":0},
            "risk_malus":{"1":0,"2":0,"3":0,"4":-1,"5":-3,"6":-5,"7":-7},
        }
    }


def test_rating_and_risk_parsers_accept_common_formats():
    assert morningstar_stars("5 stars") == 5
    assert morningstar_stars("4") == 4
    assert morningstar_stars(None) is None
    assert risk_level_7("6/7") == 6
    assert risk_level_7("7") == 7


def test_morningstar_bonus_improves_numeric_score_but_cannot_create_buy():
    decisions=pd.DataFrame([{
        "asset_class":"ETF","horizon":"CT","isin":"ETF1","score":74.0,
        "decision":"WATCH","status":"SCORABLE","notes":""
    }])
    master=pd.DataFrame([{"isin":"ETF1","morningstar_rating":5,"risk_indicator":3}])
    out=apply_etf_structural_overlay(decisions,master,_registry()).iloc[0]
    assert out["base_score"] == 74.0
    assert out["morningstar_bonus"] == 5.0
    assert out["committee_score"] == 79.0
    assert out["decision"] == "WATCH"


def test_risk_malus_can_downgrade_long_decision():
    decisions=pd.DataFrame([{
        "asset_class":"ETF","horizon":"LT","isin":"ETF1","score":79.0,
        "decision":"BUY_CANDIDATE","status":"SCORABLE","notes":""
    }])
    master=pd.DataFrame([{"isin":"ETF1","morningstar_rating":3,"risk_indicator":"7/7"}])
    out=apply_etf_structural_overlay(decisions,master,_registry()).iloc[0]
    assert out["morningstar_bonus"] == 1.0
    assert out["risk_malus"] == -7.0
    assert out["committee_score"] == 73.0
    assert out["decision"] == "WATCH"


def test_mt_core_selection_is_not_created_by_structural_bonus():
    decisions=pd.DataFrame([{
        "asset_class":"ETF","horizon":"MT","isin":"ETF1","score":79.0,
        "decision":"WATCH","status":"SCORABLE","notes":"",
        "backtest_attribution":"90.91 core only"
    }])
    master=pd.DataFrame([{"isin":"ETF1","morningstar_rating":5,"risk_indicator":1}])
    out=apply_etf_structural_overlay(decisions,master,_registry()).iloc[0]
    assert out["committee_score"] == 84.0
    assert out["decision"] == "WATCH"
    assert out["backtest_attribution"] == "90.91 core only"


def test_short_risk_inverts_quality_and_risk_adjustments():
    decisions=pd.DataFrame([{
        "asset_class":"ETF","horizon":"SHORT","isin":"ETF1","score":74.0,
        "decision":"WATCH_SHORT_RISK","status":"SCORABLE","notes":""
    }])
    master=pd.DataFrame([{"isin":"ETF1","morningstar_rating":5,"risk_indicator":7}])
    out=apply_etf_structural_overlay(decisions,master,_registry()).iloc[0]
    assert out["structural_adjustment"] == 2.0
    assert out["committee_score"] == 76.0
    assert out["decision"] == "WATCH_SHORT_RISK"
