from pathlib import Path
import pandas as pd
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from v182.decision.committee_master import load_registry, score_horizon, tct_adapter, gold_adapter, sector_ranking

def test_registry_integrity_and_t1_t2_scope():
    a=load_registry(ROOT/"config"/"V21_ACTIONS_CRITERIA_REGISTRY.json")
    e=load_registry(ROOT/"config"/"V20_7_1_ETF_CRITERIA_REGISTRY.json")
    assert a["criteria_count"] == 633
    assert e["criteria_count"] == 268
    assert e["governance"]["t1_t2_forbidden"] is True
    assert a["governance"]["t1_t2_scope"] == "ACTION_TCT_ONLY"

def test_zero_weight_criteria_are_preserved_by_policy():
    a=load_registry(ROOT/"config"/"V21_ACTIONS_CRITERIA_REGISTRY.json")
    assert a["governance"]["preserve_all_input_fields"] is True
    assert a["governance"]["zero_weight_does_not_mean_delete"] is True

def test_action_score_and_coverage_gate():
    reg={"horizons":{"CT":{"minimum_weighted_coverage":0.70,"buy_threshold":77,"watch_threshold":70,"review_threshold":60}},"criteria":[{"name":"perf","weights":{"CT":0.6},"directions":{"CT":"HIGH"}},{"name":"risk","weights":{"CT":0.4},"directions":{"CT":"LOW"}}]}
    df=pd.DataFrame({"perf":[1,2,3],"risk":[3,2,1]})
    out=score_horizon(df,reg,"CT")
    assert (out["status"]=="SCORABLE").all()
    assert out.loc[2,"score"] > out.loc[0,"score"]
    out2=score_horizon(pd.DataFrame({"perf":[1,2,3]}),reg,"CT")
    assert (out2["status"]=="BLOCK_DATA").all()

def test_tct_is_shadow_only():
    t=tct_adapter().iloc[0]
    assert t["horizon"]=="TCT"
    assert t["status"]=="SHADOW_INPUT_REQUIRED"
    assert "T1/T2 ACTION TCT only" in t["notes"]

def test_gold_missing_reference_blocks(tmp_path):
    g=gold_adapter(tmp_path/"missing_gold.json")
    assert len(g)==2
    assert set(g["status"])=={"BLOCKED_REFERENCE"}
    assert set(g["decision"])=={"ABSTAIN_BLOCKED_REFERENCE"}

def test_sector_ranking_is_within_sector_and_horizon():
    d=pd.DataFrame([{"sector":"FINANCE","asset_class":"ACTION","horizon":"MT","name":"A","isin":"1","score":80,"decision":"BUY","coverage_pct":100},{"sector":"FINANCE","asset_class":"ACTION","horizon":"MT","name":"B","isin":"2","score":90,"decision":"BUY","coverage_pct":100},{"sector":"SANTE","asset_class":"ACTION","horizon":"MT","name":"C","isin":"3","score":85,"decision":"BUY","coverage_pct":100}])
    r=sector_ranking(d)
    finance=r[(r.sector=="FINANCE") & (r.horizon=="MT")].sort_values("rank")
    assert list(finance["name"])==["B","A"]
