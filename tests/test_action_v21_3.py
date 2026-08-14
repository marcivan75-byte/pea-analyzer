from pathlib import Path
import json
import numpy as np
import pandas as pd

from v182.decision.action_overlays_v21_3 import apply_action_52w_overlay
from v182.decision.effective_weights import effective_weight_report
from v182.features.action_decision_enhancements import _morningstar_score, _threshold_gt4_score, _target_growth_score
from v182.features.ohlcv_features import calculate
from v182.features.sector_rotation import build_rotation_observations

ROOT=Path(__file__).resolve().parents[1]


def _ohlcv(close):
    idx=pd.bdate_range("2025-01-01",periods=len(close)); s=pd.Series(close,index=idx,dtype=float)
    return pd.DataFrame({"Open":s.shift(1).fillna(s),"High":s*1.005,"Low":s*0.995,"Close":s,"Volume":1_000_000.0},index=idx)


def test_52w_proximity_is_penalized_and_recovering_catchup_is_rewarded():
    near=calculate(_ohlcv(np.linspace(80,120,300))); assert near["distance_high_52w_pct"]<=0.01; assert near["high_52w_bonus_malus_points"]==-4.0
    close=np.r_[np.linspace(80,100,60),np.linspace(100,68,180),np.linspace(68,82,60)]; catch=calculate(_ohlcv(close))
    assert catch["distance_high_52w_pct"]>8.0; assert catch["perf_1m_pct"]>0; assert catch["above_mm50"] is True; assert catch["high_52w_bonus_malus_points"]>0


def test_action_overlay_preserves_etf_and_positive_challenger_cannot_create_buy():
    decisions=pd.DataFrame([
        {"asset_class":"ETF","horizon":"CT","isin":"ETF1","score":80.0,"base_score":75.0,"status":"SCORABLE","decision":"BUY_CANDIDATE"},
        {"asset_class":"ACTION","horizon":"CT","isin":"A1","score":76.0,"base_score":np.nan,"status":"SCORABLE","decision":"WATCH"},
    ])
    actions=pd.DataFrame([{"isin":"A1","high_52w_bonus_malus_points":4.0,"distance_high_52w_pct":25.0,"sector_rotation_score":70.0,"action_catchup_score":75.0,"market_high_regime_score":80.0}]); registry={"horizons":{"CT":{"buy_threshold":77,"watch_threshold":70,"review_threshold":60}}}
    out=apply_action_52w_overlay(decisions,actions,registry)
    assert out.loc[0,"base_score"]==75.0; assert out.loc[1,"base_score"]==76.0
    assert out.loc[1,"score"]==76.0; assert out.loc[1,"decision"]=="WATCH"
    assert out.loc[1,"action_52w_challenger_score"]==80.0; assert out.loc[1,"action_52w_challenger_decision"]=="BUY_CANDIDATE"


def test_action_overlay_negative_malus_can_downgrade():
    decisions=pd.DataFrame([{"asset_class":"ACTION","horizon":"CT","isin":"A1","score":78.0,"status":"SCORABLE","decision":"BUY_CANDIDATE"}]); actions=pd.DataFrame([{"isin":"A1","high_52w_bonus_malus_points":-4.0}]); registry={"horizons":{"CT":{"buy_threshold":77,"watch_threshold":70,"review_threshold":60}}}
    out=apply_action_52w_overlay(decisions,actions,registry); assert out.loc[0,"decision"]=="WATCH"; assert out.loc[0,"score"]==78.0; assert out.loc[0,"action_52w_challenger_score"]==74.0


def test_morningstar_and_gt4_threshold_are_explicit():
    assert _morningstar_score(5)>_morningstar_score(4)>_morningstar_score(3)>_morningstar_score(2)
    assert _threshold_gt4_score(3.9)==0.0; assert _threshold_gt4_score(4.1)>0.0; assert _threshold_gt4_score(8.0)>_threshold_gt4_score(4.1)
    assert _target_growth_score(20)>_target_growth_score(10)>_target_growth_score(0)


def test_effective_weights_sum_to_100_when_one_criterion_is_missing():
    frame=pd.DataFrame([{"isin":"A1","name":"A","x":10.0,"y":np.nan},{"isin":"A2","name":"B","x":20.0,"y":30.0}]); registry={"weights":{"CT":{"x":0.4,"y":0.6}},"directions":{"CT":{"x":"HIGH","y":"HIGH"}}}; report=effective_weight_report(frame,registry,"ACTION",["CT"]); a1=report[report["isin"]=="A1"]
    assert round(a1["effective_weight_pct"].sum(),6)==100.0; assert a1.loc[a1["criterion"]=="y","effective_weight_pct"].iloc[0]==0.0; assert a1.loc[a1["criterion"]=="x","effective_weight_pct"].iloc[0]==100.0


def test_sector_rotation_requires_recovery_for_high_score():
    actions=pd.DataFrame({"isin":[f"A{i}" for i in range(6)],"sector_yf":["CATCH"]*3+["FALLING"]*3,"distance_high_52w_pct":[20,22,18,30,32,28],"catchup_52w_score":[80,85,75,50,50,50],"perf_1m_pct":[5,4,6,-8,-7,-9],"perf_3m_pct":[6,5,7,-15,-14,-16],"above_mm50":[True,True,True,False,False,False],"above_mm200":[True,True,False,False,False,False]}); _,sectors,_=build_rotation_observations(actions); catch=float(sectors.loc[sectors["sector"]=="CATCH","sector_rotation_score"].iloc[0]); falling=float(sectors.loc[sectors["sector"]=="FALLING","sector_rotation_score"].iloc[0]); assert catch>50; assert falling<=50


def test_v21_7_action_weight_sets_match_study_hardened_governance():
    cfg=json.loads((ROOT/"config"/"V21_ACTIONS_CRITERIA_REGISTRY.json").read_text())
    for horizon in ("CT","MT","LT","SHORT","TOP_DOWN"): assert abs(sum(cfg["weights"][horizon].values())-1.0)<1e-6
    for horizon in ("CT","MT","LT"):
        assert "morningstar_action_score" not in cfg["weights"][horizon]
        assert "dividend_gt4_score" not in cfg["weights"][horizon]
        assert "target_upside_gt4_score" not in cfg["weights"][horizon]
        assert "total_return_potential_score" not in cfg["weights"][horizon]
    assert cfg["weights"]["MT"]["target_upside_pct_v21"]>0
    assert cfg["weights"]["MT"]["dividend_yield_v21_pct"]>0
    assert cfg["governance"]["positive_unvalidated_overlay_can_create_buy"] is False
