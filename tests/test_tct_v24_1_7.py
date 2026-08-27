from pathlib import Path
import pandas as pd

from v182.decision.tct_v24_1_7 import (
    parse_pea_eligibility, universe_gate,
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
        "tct_baseline_rank":5,"tct_baseline_coverage":0.84,
        "bb_squeeze_consecutive_sessions":5,"rvol20":1.5,"volume_increase_flag":True,"bb_breakout_cross_flag":True,
        "bb_bandwidth_expansion_ratio":1.10,
        "last_close":101.0,"bb_upper":100.0,"atr14":2.0,
        "macd_hist":-0.04,"macd_hist_rising_share_3":0.50,
        "stoch_k":60.0,"stoch_d":55.0,"stoch_bull_cross_flag":True,"rsi14":60.0,"mm50":95.0,"sar":96.0,
        "bb_bandwidth":1.0,"relative_strength_10d":0.02,
    }
    for field in CFG["t1"]["component_fields"].values(): row[field]=80.0
    return pd.Series(row)


def test_t1_exact_gates_and_quality_reach_shadow_starter():
    result=evaluate_t1(_t1_row(),CFG)
    assert result["status"] == "SHADOW_T1_ELIGIBLE"
    assert result["decision"] == "T1_STARTER_25_SHADOW"
    assert result["quality_score"] == 80.0


def test_t1_requires_sar_and_mm50_and_bandwidth_expansion():
    row=_t1_row().copy(); row["sar"]=102.0
    assert "TECHNICAL_GATE_FAIL" in evaluate_t1(row,CFG)["reasons"]
    row=_t1_row().copy(); row["mm50"]=102.0
    assert "TECHNICAL_GATE_FAIL" in evaluate_t1(row,CFG)["reasons"]
    row=_t1_row().copy(); row["bb_bandwidth_expansion_ratio"]=0.99
    assert "BANDWIDTH_NOT_EXPANDING" in evaluate_t1(row,CFG)["reasons"]


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
    row["bb_bandwidth"]=1.20; row["bb_bandwidth_expanding_flag"]=True; row["macd_hist"]=0.05; row["macd_bull_cross_flag"]=True; row["rvol20"]=1.4
    row["last_close"]=103.0; row["bb_upper"]=102.0; row["atr14"]=3.0; row["relative_strength_10d"]=-0.02
    for field in CFG["t2"]["component_fields"].values(): row[field]=80.0
    result=evaluate_t2(row,state,5,CFG)
    assert result["status"] == "SHADOW_T2_CONFIRMED"
    assert result["decision"] == "T2_CONFIRM_75_SHADOW"
    assert "RELATIVE_STRENGTH_DEGRADED" not in result.get("reasons",[])
    no_state=evaluate_t2(row,None,5,CFG)
    assert "EXACT_LINKED_T1_REQUIRED" in no_state["reasons"]


def test_t2_ttl_source_semantics_allow_age_zero_but_reject_gt_10():
    t1_eval=evaluate_t1(_t1_row(),CFG); state=make_t1_state(_t1_row(),t1_eval,"2026-08-12")
    row=_t1_row().copy(); row["bb_bandwidth"]=1.20; row["bb_bandwidth_expanding_flag"]=True; row["macd_hist"]=0.05; row["macd_bull_cross_flag"]=True; row["rvol20"]=1.4; row["last_close"]=103.0; row["bb_upper"]=102.0; row["atr14"]=3.0
    for field in CFG["t2"]["component_fields"].values(): row[field]=80.0
    assert evaluate_t2(row,state,0,CFG)["status"] == "SHADOW_T2_CONFIRMED"
    assert "T1_TTL_FAIL" in evaluate_t2(row,state,11,CFG)["reasons"]


def test_tct_snapshot_without_baseline_is_explicitly_blocked():
    out=tct_shadow_snapshot(pd.DataFrame([{"isin":"FR1","pea_eligible":True}]),CFG)
    assert out.iloc[0]["status"] == "SHADOW_BASELINE_REQUIRED"
