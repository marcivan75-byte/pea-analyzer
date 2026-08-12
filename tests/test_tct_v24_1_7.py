from pathlib import Path
import pandas as pd

from v182.decision.tct_v24_1_7 import (
    parse_pea_eligibility, universe_gate, weighted_quality,
    evaluate_t1, make_t1_state, evaluate_t2, tct_shadow_snapshot, load_tct_config,
)

ROOT=Path(__file__).resolve().parents[1]
CFG=load_tct_config(ROOT/"config"/"TCT_V24_1_7_SHADOW.json")


def test_pea_string_false_is_quarantined_and_true_passes():
    assert parse_pea_eligibility("false") is False
    assert parse_pea_eligibility("no") is False
    assert parse_pea_eligibility("true") is True
    assert parse_pea_eligibility("yes") is True
    assert parse_pea_eligibility(0.3) is False
    assert parse_pea_eligibility(0.8) is True
    assert universe_gate(pd.Series({"pea_eligible":"false","asset_class":"ACTION"})).status == "QUARANTINE"
    assert universe_gate(pd.Series({"pea_eligible":"true","asset_class":"ACTION"})).status == "PASS"


def _t1_row():
    row={
        "isin":"FRTEST000001","name":"TEST","asset_class":"ACTION","pea_eligible":"true",
        "tct_baseline_rank":5,"tct_baseline_coverage":0.90,
        "bb_squeeze_fraction_8":0.875,"rvol20":1.5,"bb_breakout_cross_flag":True,
        "last_close":101.0,"bb_upper":100.0,"atr14":2.0,
        "macd_hist":-0.04,"macd_hist_3d_ago":-0.10,
        "stoch_k":60.0,"stoch_d":55.0,"rsi14":60.0,"mm50":95.0,
        "bb_bandwidth":1.0,"relative_strength_10d":2.0,
    }
    for field in CFG["t1"]["component_fields"].values(): row[field]=80.0
    return pd.Series(row)


def test_t1_exact_gates_and_quality_reach_shadow_starter():
    result=evaluate_t1(_t1_row(),CFG)
    assert result["status"] == "SHADOW_T1_ELIGIBLE"
    assert result["decision"] == "T1_STARTER_25_SHADOW"
    assert result["quality_score"] == 80.0


def test_t1_requires_quality_components_instead_of_inventing_them():
    row=_t1_row().drop(labels=list(CFG["t1"]["component_fields"].values()))
    result=evaluate_t1(row,CFG)
    assert result["status"] == "SHADOW_INPUT_REQUIRED"
    assert "T1_QUALITY_COMPONENTS" in result["missing"]


def test_t2_requires_exact_t1_and_confirms_valid_state():
    t1_eval=evaluate_t1(_t1_row(),CFG)
    state=make_t1_state(_t1_row(),t1_eval,"2026-08-12")
    assert state is not None
    row=_t1_row().copy()
    row["bb_bandwidth"]=1.20; row["macd_hist"]=0.05; row["rvol20"]=1.4
    row["last_close"]=103.0; row["bb_upper"]=102.0; row["atr14"]=3.0; row["relative_strength_10d"]=2.2
    for field in CFG["t2"]["component_fields"].values(): row[field]=80.0
    result=evaluate_t2(row,state,5,CFG)
    assert result["status"] == "SHADOW_T2_CONFIRMED"
    assert result["decision"] == "T2_CONFIRM_75_SHADOW"
    no_state=evaluate_t2(row,None,5,CFG)
    assert "EXACT_LINKED_T1_REQUIRED" in no_state["reasons"]


def test_tct_snapshot_without_baseline_is_explicitly_blocked():
    out=tct_shadow_snapshot(pd.DataFrame([{"isin":"FR1","pea_eligible":True}]),CFG)
    assert out.iloc[0]["status"] == "SHADOW_BASELINE_REQUIRED"
