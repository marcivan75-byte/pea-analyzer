from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd

from v182.reporting import etf_fund_flows_shadow_run as flow_runner
from v182.reporting import waves
from v182.reporting.horizon_cache_policy import (
    assign_refresh_tiers,
    previous_horizon_candidates,
    write_horizon_priority_state,
)
from v182.sources import finnhub_consensus as finnhub
from v182.sources import yfinance_info as yahoo_info


def _yahoo_rows(tickers: list[str]) -> list[dict]:
    return [
        {"ticker": ticker, "field": "trailing_eps_yf", "value": 2.0, "source": "yfinance"}
        for ticker in tickers
    ]


def _finnhub_rows(tickers: list[str], target_tickers: set[str]) -> list[dict]:
    rows=[]
    for ticker in tickers:
        rows.append({"ticker":ticker,"field":"consensus_score","value":4.0,"source":"Finnhub"})
        if ticker in target_tickers:
            rows.append({"ticker":ticker,"field":"target_price","value":120.0,"source":"Finnhub"})
    return rows


def test_yahoo_info_cache_bootstraps_once_then_refreshes_hot_first(tmp_path, monkeypatch) -> None:
    calls: list[list[str]]=[]

    def fake_collect(tickers, delay_seconds=0.4, max_workers=4):
        calls.append(list(tickers))
        return _yahoo_rows(list(tickers)), []

    monkeypatch.setattr(yahoo_info,"collect_info",fake_collect)
    cache=tmp_path/"yahoo.json"
    day0=datetime(2026,8,1,12,0,tzinfo=timezone.utc)
    tickers=["HOT.PA","WARM.PA","COLD.PA"]
    tiers={"HOT.PA":"HOT","WARM.PA":"WARM","COLD.PA":"COLD"}

    _,failures,metrics=yahoo_info.collect_info_cached(
        tickers,cache,priority_tiers=tiers,ttl_days={"HOT":3,"WARM":7,"COLD":21},refresh_budget=1,now=day0,
    )
    assert failures==[]
    assert calls==[sorted(tickers)]
    assert metrics["mandatory_refresh_count"]==3
    calls.clear()

    _,_,metrics=yahoo_info.collect_info_cached(
        tickers,cache,priority_tiers=tiers,ttl_days={"HOT":3,"WARM":7,"COLD":21},refresh_budget=1,now=day0+timedelta(days=1),
    )
    assert calls==[]
    assert metrics["live_refresh_requested"]==0
    assert metrics["cache_hit_tickers"]==3

    _,_,metrics=yahoo_info.collect_info_cached(
        tickers,cache,priority_tiers=tiers,ttl_days={"HOT":3,"WARM":7,"COLD":21},refresh_budget=1,now=day0+timedelta(days=4),
    )
    assert calls==[["HOT.PA"]]
    assert metrics["live_refresh_requested"]==1


def test_action_refresh_tiers_and_local_ratios_do_not_change_scores() -> None:
    frame=pd.DataFrame({
        "yahoo_ticker":["AAA.PA","BBB.PA","CCC.PA"],
        "score_brut":[10,90,50],
        "comite_status":["","WATCH",""],
        "earnings_within_7d_flag":[0,0,1],
    })
    tiers=waves._action_refresh_tiers(frame,warm_n=1)
    assert tiers["BBB.PA"]=="HOT"
    assert tiers["CCC.PA"]=="HOT"
    assert tiers["AAA.PA"]=="COLD"
    assert waves._positive_ratio(100,5)==20.0
    assert waves._positive_ratio(100,-5) is None


def test_horizon_priority_state_persists_only_valid_action_etf_rankings(tmp_path) -> None:
    state_path=tmp_path/"state"/"provenance"/"HORIZON_REFRESH_PRIORITY_V1.csv"
    decisions=pd.DataFrame([
        {"asset_class":"ACTION","horizon":"TCT","isin":"I1","score":99,"status":"OK","decision":"WATCH"},
        {"asset_class":"ACTION","horizon":"CT","isin":"I2","score":95,"status":"OK","decision":"BUY"},
        {"asset_class":"ETF","horizon":"MT","isin":"E1","score":90,"status":"OK","decision":"WATCH"},
        {"asset_class":"GOLD","horizon":"LT","isin":"G1","score":88,"status":"OK","decision":"WATCH"},
        {"asset_class":"ACTION","horizon":"LT","isin":"I3","score":85,"status":"FAILED","decision":"FAILED"},
    ])
    audit=write_horizon_priority_state(decisions,state_path,generated_at_utc="2026-08-22T08:00:00+00:00")
    state=pd.read_csv(state_path,sep=";",dtype=str)
    assert audit["status"]=="SUCCESS"
    assert audit["decision_logic_changed"] is False
    assert set(state["isin"])=={"I1","I2","E1"}
    assert set(state["asset_class"])=={"ACTION","ETF"}
    assert set(state["generated_at_utc"])=={"2026-08-22T08:00:00+00:00"}


def test_horizon_policy_uses_previous_ct_before_mt_without_using_tct_for_fundamentals(tmp_path) -> None:
    state_path=tmp_path/"state"/"provenance"/"HORIZON_REFRESH_PRIORITY_V1.csv"
    decisions=pd.DataFrame([
        {"asset_class":"ACTION","horizon":"TCT","isin":"I1","score":99,"status":"OK","decision":"WATCH"},
        {"asset_class":"ACTION","horizon":"CT","isin":"I2","score":95,"status":"OK","decision":"WATCH"},
        {"asset_class":"ACTION","horizon":"MT","isin":"I3","score":90,"status":"OK","decision":"WATCH"},
        {"asset_class":"ACTION","horizon":"LT","isin":"I4","score":85,"status":"OK","decision":"WATCH"},
    ])
    write_horizon_priority_state(decisions,state_path)
    frame=pd.DataFrame({"isin":["I1","I2","I3","I4"],"yahoo_ticker":["T1","T2","T3","T4"]})
    policy={
        "previous_decisions_path":"state/provenance/HORIZON_REFRESH_PRIORITY_V1.csv",
        "consumer_horizons":["CT","MT","LT"],
        "hot_horizons":["CT"],
        "warm_horizons":["MT"],
        "candidate_limits":{"TCT":10,"CT":10,"MT":10,"LT":10},
        "promotion_buffer_top_n":0,
    }
    tiers,audit=assign_refresh_tiers(frame,tmp_path,asset_class="ACTION",policy=policy,fallback_warm_n=0)
    assert tiers=={"T1":"COLD","T2":"HOT","T3":"WARM","T4":"COLD"}
    assert audit["mode"]=="PREVIOUS_HORIZON_RANKING"
    assert audit["full_universe_preserved"] is True
    assert audit["decision_logic_changed"] is False


def test_horizon_policy_falls_back_safely_when_previous_decisions_are_missing(tmp_path) -> None:
    frame=pd.DataFrame({
        "isin":["I1","I2","I3"],
        "yahoo_ticker":["T1","T2","T3"],
        "score_brut":[10,90,50],
        "earnings_within_7d_flag":[0,0,1],
    })
    tiers,audit=assign_refresh_tiers(
        frame,tmp_path,asset_class="ACTION",
        policy={"consumer_horizons":["CT","MT","LT"],"promotion_buffer_top_n":1},
        fallback_warm_n=1,
    )
    assert tiers["T2"]=="WARM"
    assert tiers["T3"]=="HOT"
    assert tiers["T1"]=="COLD"
    assert audit["mode"]=="FALLBACK_NO_PREVIOUS_DECISIONS"


def test_previous_horizon_candidates_respects_per_horizon_limits(tmp_path) -> None:
    state_path=tmp_path/"state"/"provenance"/"HORIZON_REFRESH_PRIORITY_V1.csv"
    write_horizon_priority_state(pd.DataFrame([
        {"asset_class":"ETF","horizon":"CT","isin":"E1","score":90,"status":"OK","decision":"WATCH"},
        {"asset_class":"ETF","horizon":"CT","isin":"E2","score":80,"status":"OK","decision":"WATCH"},
        {"asset_class":"ETF","horizon":"MT","isin":"E3","score":70,"status":"OK","decision":"WATCH"},
    ]),state_path)
    candidates,audit=previous_horizon_candidates(tmp_path,"ETF",limits={"CT":1,"MT":1,"LT":2})
    assert candidates["CT"]=={"E1"}
    assert candidates["MT"]=={"E3"}
    assert audit["selected_by_horizon"]["CT"]==1


def test_finnhub_recommendation_refresh_can_skip_target_refresh(tmp_path, monkeypatch) -> None:
    calls=[]

    def fake_fetch(tickers, api_key, delay_seconds=1.1, max_workers=8, *, target_tickers=None):
        target_set=set(tickers) if target_tickers is None else set(target_tickers)
        calls.append((list(tickers),target_set))
        return _finnhub_rows(list(tickers),target_set),[]

    monkeypatch.setattr(finnhub,"fetch_consensus",fake_fetch)
    cache=tmp_path/"finnhub.json"
    day0=datetime(2026,8,1,12,0,tzinfo=timezone.utc)
    tickers=["AAA","BBB"]

    _,failures,metrics=finnhub.fetch_consensus_cached(
        tickers,"key",cache,refresh_budget=10,recommendation_ttl_days=3,target_ttl_days=7,target_refresh_budget=10,max_cache_age_days=14,now=day0,
    )
    assert failures==[]
    assert calls[-1]==(tickers,set(tickers))
    assert metrics["target_live_refresh_requested"]==2

    calls.clear()
    observations,failures,metrics=finnhub.fetch_consensus_cached(
        tickers,"key",cache,refresh_budget=10,recommendation_ttl_days=3,target_ttl_days=7,target_refresh_budget=10,max_cache_age_days=14,now=day0+timedelta(days=4),
    )
    assert failures==[]
    assert calls==[(tickers,set())]
    assert metrics["live_refresh_requested"]==2
    assert metrics["target_live_refresh_requested"]==0
    assert metrics["target_calls_avoided"]==2
    target_rows=[row for row in observations if row["field"]=="target_price"]
    assert len(target_rows)==2
    assert {row["cache_state"] for row in target_rows}=={"CACHE_HIT"}
    assert {row["fetched_at_utc"] for row in target_rows}=={day0.isoformat()}


def test_parallel_fund_flow_collector_preserves_all_instruments(monkeypatch) -> None:
    universe=pd.DataFrame({
        "instrument_id":[f"I{i}" for i in range(6)],
        "ticker":[f"T{i}" for i in range(6)],
    })

    def fake_collect(chunk, official_input=None, delay_seconds=0.05):
        rows=[]
        for _,row in chunk.iterrows():
            rows.append({
                "instrument_id":row["instrument_id"],"as_of":"2026-08-22","source_priority":50,
                "confidence":"C","source":"test","source_type":"TEST","aum":1.0,"nav":1.0,
                "shares_outstanding":1.0,"market_price":1.0,"distribution_per_share":0.0,
            })
        return pd.DataFrame(rows),pd.DataFrame()

    monkeypatch.setattr(flow_runner,"collect_current_snapshot",fake_collect)
    snapshot,failures,metrics=flow_runner._collect_snapshot_parallel(
        universe,pd.DataFrame(),max_workers=3,chunk_size=2,delay_seconds=0.0,
    )
    assert failures.empty
    assert set(snapshot["instrument_id"])==set(universe["instrument_id"])
    assert metrics["chunks"]==3
    assert metrics["workers"]==3
    assert metrics["mode"]=="BOUNDED_PARALLEL_CHUNKS"
    assert metrics["history_rebuilt"] is False


def test_master_config_registers_runtime_optimization_policy() -> None:
    cfg=json.loads(Path("config/V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    assert cfg["version"]=="21.13.3"
    opt=cfg["runtime_optimization"]
    assert opt["status"]=="ACTIVE_V21_13_3_HORIZON_AWARE"
    assert opt["yfinance_fundamentals"]["ttl_days"]=={"HOT":3,"WARM":10,"COLD":21}
    assert opt["finnhub_consensus"]["tiers"]["HOT"]["target_ttl_days"] > opt["finnhub_consensus"]["tiers"]["HOT"]["recommendation_ttl_days"]
    assert opt["etf_info"]["ttl_days"]=={"HOT":7,"WARM":14,"COLD":30}
    policy=opt["horizon_data_policy"]
    assert policy["previous_decisions_path"]=="state/provenance/HORIZON_REFRESH_PRIORITY_V1.csv"
    assert policy["state_written_after_committee"] is True
    assert policy["source_families"]["ACTION_FUNDAMENTALS"]["consumer_horizons"]==["CT","MT","LT"]
    assert "TCT" not in policy["source_families"]["ACTION_CONSENSUS"]["consumer_horizons"]
    assert policy["source_families"]["OHLCV"]["cadence"]=="EACH_TRADING_DAY"
    assert 6 <= opt["etf_fund_flows"]["max_workers"] <= 8
