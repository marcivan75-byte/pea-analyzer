import pandas as pd

from v182.decision.etf_structural_overlay import (
    morningstar_stars, risk_level_7, apply_etf_structural_overlay,
)


def _registry():
    return {
        "horizons":{
            "CT":{"buy_threshold":77,"watch_threshold":70,"review_threshold":60},
            "MT":{"selection_threshold":82,"watch_threshold":70,"review_threshold":60},
            "SHORT":{"short_candidate_threshold":77,"watch_threshold":70},
        },
        "bonus_malus":{
            "morningstar_bonus":{"5":0,"4":3,"3":1,"2":0,"1":0,"unrated":0},
            "risk_malus":{"1":0,"2":0,"3":0,"4":0,"5":0,"6":-3,"7":-3},
        }
    }


def test_rating_and_risk_parsers_accept_common_formats():
    assert morningstar_stars("5 stars") == 5
    assert morningstar_stars("4") == 4
    assert morningstar_stars(None) is None
    assert risk_level_7("6/7") == 6
    assert risk_level_7("7") == 7


def test_gitok_morningstar_rule_4star_bonus_3star_smaller_others_neutral():
    decisions=pd.DataFrame([
        {"asset_class":"ETF","horizon":"CT","isin":"E5","score":74.0,"decision":"WATCH","status":"SCORABLE","notes":""},
        {"asset_class":"ETF","horizon":"CT","isin":"E4","score":74.0,"decision":"WATCH","status":"SCORABLE","notes":""},
        {"asset_class":"ETF","horizon":"CT","isin":"E3","score":74.0,"decision":"WATCH","status":"SCORABLE","notes":""},
    ])
    master=pd.DataFrame([
        {"isin":"E5","morningstar_rating":5,"risk_indicator":3},
        {"isin":"E4","morningstar_rating":4,"risk_indicator":3},
        {"isin":"E3","morningstar_rating":3,"risk_indicator":3},
    ])
    out=apply_etf_structural_overlay(decisions,master,_registry()).set_index("isin")
    assert out.loc["E5","morningstar_bonus"] == 0.0
    assert out.loc["E5","committee_score"] == 74.0
    assert out.loc["E4","morningstar_bonus"] == 3.0
    assert out.loc["E4","committee_score"] == 77.0
    assert out.loc["E3","morningstar_bonus"] == 1.0
    assert out.loc["E3","committee_score"] == 75.0
    # Positive bonus cannot create BUY by itself.
    assert out.loc["E4","decision"] == "WATCH"


def test_audited_sri_rule_only_6_or_7_get_minus_3():
    decisions=pd.DataFrame([
        {"asset_class":"ETF","horizon":"CT","isin":"E5","score":79.0,"decision":"BUY_CANDIDATE","status":"SCORABLE","notes":""},
        {"asset_class":"ETF","horizon":"CT","isin":"E6","score":79.0,"decision":"BUY_CANDIDATE","status":"SCORABLE","notes":""},
        {"asset_class":"ETF","horizon":"CT","isin":"E7","score":79.0,"decision":"BUY_CANDIDATE","status":"SCORABLE","notes":""},
    ])
    master=pd.DataFrame([
        {"isin":"E5","morningstar_rating":2,"risk_indicator":5},
        {"isin":"E6","morningstar_rating":2,"risk_indicator":6},
        {"isin":"E7","morningstar_rating":2,"risk_indicator":7},
    ])
    out=apply_etf_structural_overlay(decisions,master,_registry()).set_index("isin")
    assert out.loc["E5","risk_malus"] == 0.0
    assert out.loc["E6","risk_malus"] == -3.0
    assert out.loc["E7","risk_malus"] == -3.0
    assert out.loc["E6","committee_score"] == 76.0
    assert out.loc["E6","decision"] == "WATCH"


def test_mt_core_selection_is_not_created_by_structural_bonus():
    decisions=pd.DataFrame([{
        "asset_class":"ETF","horizon":"MT","isin":"ETF1","score":80.0,
        "decision":"WATCH","status":"SCORABLE","notes":"",
        "backtest_attribution":"90.91 core only"
    }])
    master=pd.DataFrame([{"isin":"ETF1","morningstar_rating":4,"risk_indicator":1}])
    out=apply_etf_structural_overlay(decisions,master,_registry()).iloc[0]
    assert out["committee_score"] == 83.0
    assert out["decision"] == "WATCH"
    assert out["backtest_attribution"] == "90.91 core only"


def test_short_risk_inverts_quality_and_observed_sri_malus():
    decisions=pd.DataFrame([{
        "asset_class":"ETF","horizon":"SHORT","isin":"ETF1","score":74.0,
        "decision":"WATCH_SHORT_RISK","status":"SCORABLE","notes":""
    }])
    master=pd.DataFrame([{"isin":"ETF1","morningstar_rating":4,"risk_indicator":7}])
    out=apply_etf_structural_overlay(decisions,master,_registry()).iloc[0]
    # +3 quality decreases short-risk by 3, SRI>=6 increases it by 3: net zero.
    assert out["structural_adjustment"] == 0.0
    assert out["committee_score"] == 74.0
    assert out["decision"] == "WATCH_SHORT_RISK"
