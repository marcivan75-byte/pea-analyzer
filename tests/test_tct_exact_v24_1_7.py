from pathlib import Path
import json
import pandas as pd

import v182.decision.tct_timing_exact_v24_1_7 as exact
from v182.decision.tct_timing_exact_v24_1_7 import (
    FORMULA_VERSION,T1_WEIGHTS,T2_WEIGHTS,detect_exact,build_exact_timing_snapshot,load_state,
)


def _cfg():
    return {
        "formula_version":FORMULA_VERSION,
        "scope":{"baseline_top_n":20,"baseline_min_coverage":0.60},
        "squeeze":{"percentile":0.15,"lookback_sessions":100,"window_sessions":8,"minimum_fraction_below_threshold":0.80},
        "t1":{"quality_threshold":70.0,"starter_threshold":80.0,"quality_component_min_coverage":0.80,"ttl_sessions":10,"rvol_min":1.20,"max_extension_atr":1.5,"max_extension_pct":0.10},
        "t2":{"quality_threshold":75.0,"quality_component_min_coverage":0.80,"bandwidth_expansion_ratio_min":1.15,"rvol_min":1.20,"max_extension_atr":1.5,"max_extension_pct":0.10,"hold_floor_bb_factor":0.995,"hold_floor_t1_factor":0.98},
    }


def _history(macd_tail=(-0.30,-0.20,-0.05),current_close=10.30,current_bb=10.20,current_bw=0.02,rs=0.04):
    n=120
    frame=pd.DataFrame(index=pd.bdate_range("2026-01-02",periods=n))
    frame["bandwidth"]=0.04
    frame.loc[frame.index[-9:-1],"bandwidth"]=0.01
    frame.loc[frame.index[-1],"bandwidth"]=current_bw
    frame["close"]=10.0; frame.loc[frame.index[-1],"close"]=current_close
    frame["open"]=frame["close"].shift(1).fillna(10.0)
    frame["high"]=frame[["open","close"]].max(axis=1)+0.10
    frame["low"]=frame[["open","close"]].min(axis=1)-0.10
    frame["volume"]=100.0; frame.loc[frame.index[-1],"volume"]=220.0
    frame["bb_high"]=10.10; frame.loc[frame.index[-1],"bb_high"]=current_bb
    frame["stoch_k"]=60.0; frame["stoch_d"]=50.0
    frame["macd"]=-0.30; frame["macd_signal"]=0.0
    frame.loc[frame.index[-3:],"macd"]=list(macd_tail)
    frame["macd_hist"]=frame["macd"]-frame["macd_signal"]
    frame["rsi"]=60.0; frame["sar"]=9.0; frame["mm50"]=9.5
    frame["atr_14"]=0.40; frame["atr_pct"]=frame["atr_14"]/frame["close"]
    frame["rs_10d"]=rs
    return frame


def _actions(top20=True):
    return pd.DataFrame([{
        "isin":"FR0000000001","name":"TEST ACTION","asset_class":"ACTION","pea_eligible":True,
        "yahoo_ticker":"TEST.PA","tct_baseline_rank":1 if top20 else 21,"tct_baseline_coverage":0.84,
        "sector_yf":"Technology",
    }])


def _write_history(cache:Path,tech:pd.DataFrame):
    cache.mkdir(parents=True,exist_ok=True)
    pd.concat({"TEST.PA":tech},axis=1).to_parquet(cache/"history_00000.parquet")


def test_exact_quality_weights_match_source_kit():
    assert abs(sum(T1_WEIGHTS.values())-1.0)<1e-12
    assert abs(sum(T2_WEIGHTS.values())-1.0)<1e-12
    assert T1_WEIGHTS=={"compression":0.25,"volume_acceleration":0.20,"breakout_quality":0.20,"momentum_acceleration":0.15,"relative_strength":0.10,"risk_control":0.10}
    assert T2_WEIGHTS=={"bandwidth_expansion":0.25,"macd_confirmation":0.20,"volume_persistence":0.20,"breakout_hold":0.15,"relative_strength_continuation":0.10,"non_extension":0.10}


def test_source_scenario_detects_t1_and_linked_t2():
    t1=detect_exact(_history(),{},_cfg())
    assert t1["setup"]=="T1"
    assert t1["t1_quality"]>=80.0
    state={**t1["state_update"],"event_id":"T1_TEST","baseline_eligible_at_t1":True,"age_sessions":5}
    t2=detect_exact(_history((-0.05,0.01,0.08),10.45,10.35,0.024,rs=0.04),state,_cfg())
    assert t2["setup"]=="T2_CONFIRMATION"
    assert t2["t2_quality"]>=75.0
    assert t2["source_event_id"]=="T1_TEST"


def test_t1_technical_gate_requires_both_sar_and_mm50_and_bandwidth_expansion():
    hist=_history(); hist.loc[hist.index[-1],"sar"]=10.40
    assert detect_exact(hist,{},_cfg())["setup"] is None
    hist=_history(); hist.loc[hist.index[-1],"mm50"]=10.40
    assert detect_exact(hist,{},_cfg())["setup"] is None
    hist=_history(current_bw=0.009)  # previous squeeze bandwidth is 0.01
    assert detect_exact(hist,{},_cfg())["setup"] is None


def test_relative_strength_degradation_is_t2_quality_component_not_binary_gate():
    t1=detect_exact(_history(),{},_cfg())
    state={**t1["state_update"],"event_id":"T1_TEST","baseline_eligible_at_t1":True,"age_sessions":5,"rs_10d_at_t1":0.05}
    t2=detect_exact(_history((-0.05,0.01,0.08),10.45,10.35,0.024,rs=0.02),state,_cfg())
    assert t2["t2_components"]["relative_strength_continuation"] < 70.0
    assert t2["setup"]=="T2_CONFIRMATION"


def test_t1_is_persisted_only_after_current_baseline_top20(monkeypatch,tmp_path):
    monkeypatch.setattr(exact,"compute_technical_indicators",lambda frame: frame)
    cache=tmp_path/"cache"; state_path=tmp_path/"state.json"; _write_history(cache,_history())
    blocked,audit=build_exact_timing_snapshot(_actions(top20=False),cache,state_path,_cfg())
    assert blocked.iloc[0]["status"]=="BLOCKED_BASELINE"
    assert audit.t1_detected_raw==1 and audit.t1_baseline_eligible==0
    assert json.loads(state_path.read_text())=={}

    eligible,audit=build_exact_timing_snapshot(_actions(top20=True),cache,state_path,_cfg())
    assert eligible.iloc[0]["status"]=="T1_STARTER_25_SHADOW"
    assert audit.t1_baseline_eligible==1
    saved=json.loads(state_path.read_text())
    assert saved["FR0000000001"]["baseline_eligible_at_t1"] is True
    assert saved["FR0000000001"]["event_id"].startswith("T1_")


def test_t2_requires_persisted_eligible_t1_and_consumes_only_when_current_baseline_ok(monkeypatch,tmp_path):
    monkeypatch.setattr(exact,"compute_technical_indicators",lambda frame: frame)
    cache=tmp_path/"cache"; state_path=tmp_path/"state.json"; _write_history(cache,_history())
    build_exact_timing_snapshot(_actions(top20=True),cache,state_path,_cfg())

    _write_history(cache,_history((-0.05,0.01,0.08),10.45,10.35,0.024))
    blocked,audit=build_exact_timing_snapshot(_actions(top20=False),cache,state_path,_cfg())
    assert blocked.iloc[0]["status"]=="BLOCKED_BASELINE"
    assert audit.t2_confirmed==0
    assert "FR0000000001" in json.loads(state_path.read_text())

    confirmed,audit=build_exact_timing_snapshot(_actions(top20=True),cache,state_path,_cfg())
    assert confirmed.iloc[0]["status"]=="T2_CONFIRM_75_SHADOW"
    assert audit.t2_confirmed==1
    assert json.loads(state_path.read_text())=={}


def test_state_ttl_prunes_old_records(tmp_path):
    path=tmp_path/"state.json"
    path.write_text(json.dumps({"FR0000000001":{"bandwidth":0.02,"detected_at":"2026-01-01","event_id":"T1_X","baseline_eligible_at_t1":True}}))
    state,expired=load_state(path,ttl_sessions=10,as_of=pd.Timestamp("2026-08-12").date())
    assert state=={} and expired==1
