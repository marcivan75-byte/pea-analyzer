from pathlib import Path
import pandas as pd
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))

from v182.decision.committee_master import (
    load_registry, score_horizon, tct_adapter, sector_ranking,
    resolve_field, criterion_coverage_report,
)
from v182.audit.quality import run_quality_gates
from v182.sources.yfinance_info import FIELDS

def test_registry_integrity_and_t1_t2_scope():
    a=load_registry(ROOT/"config"/"V21_ACTIONS_CRITERIA_REGISTRY.json")
    e=load_registry(ROOT/"config"/"V20_7_1_ETF_CRITERIA_REGISTRY.json")
    assert a["criteria_count"] == 633
    assert e["criteria_count"] == 268
    assert e["governance"]["t1_t2_forbidden"] is True
    assert a["governance"]["t1_t2_scope"] == "ACTION_TCT_ONLY"
    assert "LT" not in a["horizons"] and "LT" not in e["horizons"]

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

def test_canonical_aliases_and_exact_derivations():
    df=pd.DataFrame({
        "per_forward_yf":[10.0,20.0],
        "roe_api":[0.20,0.10],
        "consensus_score":[4.0,3.0],
        "target_price":[120.0,90.0],
        "last_close":[100.0,100.0],
        "free_cash_flow":[10.0,5.0],
        "market_cap":[100.0,100.0],
        "total_debt_yf":[40.0,30.0],
        "ebitda_yf":[20.0,10.0],
        "revenue_growth_yf":[0.12,0.05],
        "earnings_growth_yf":[0.15,0.02],
    })
    cases={
        "per_forward_v21":"ALIAS:per_forward_yf",
        "roe_v21_pct":"ALIAS:roe_api",
        "consensus_score_100_v21":"DERIVED:consensus_score*20",
        "target_upside_pct_v21":"DERIVED:target_price/last_close",
        "fcf_yield_v21":"DERIVED:free_cash_flow/market_cap",
        "debt_to_ebitda_v21":"DERIVED:total_debt_yf/ebitda_yf",
        "revenue_growth_v21_pct":"ALIAS:revenue_growth_yf",
        "earnings_growth_v21_pct":"ALIAS:earnings_growth_yf",
    }
    for field, expected_source in cases.items():
        values, source=resolve_field(df,field)
        assert values is not None and values.notna().all()
        assert source == expected_source
    assert resolve_field(pd.DataFrame({"croiss_ca_3y":[5.0]}),"revenue_growth_v21_pct")[0] is None
    assert resolve_field(pd.DataFrame({"croiss_eps_3y":[5.0]}),"earnings_growth_v21_pct")[0] is None

def test_yfinance_collects_exact_v21_inputs_without_renaming_semantics():
    assert FIELDS["revenueGrowth"] == "revenue_growth_yf"
    assert FIELDS["earningsGrowth"] == "earnings_growth_yf"
    assert FIELDS["totalDebt"] == "total_debt_yf"
    assert FIELDS["ebitda"] == "ebitda_yf"
    assert FIELDS["payoutRatio"] == "payout_ratio"

def test_criterion_coverage_reports_resolution_source():
    reg={"weights":{"MT":{"per_forward_v21":0.5,"revenue_growth_v21_pct":0.5}},"directions":{"MT":{"per_forward_v21":"LOW","revenue_growth_v21_pct":"HIGH"}},"horizons":{"MT":{"minimum_weighted_coverage":0.7}}}
    df=pd.DataFrame({"per_forward_yf":[10.0,20.0],"revenue_growth_yf":[0.1,None]})
    report=criterion_coverage_report(df,reg,"ACTION",["MT"])
    assert set(report["criterion"]) == {"per_forward_v21","revenue_growth_v21_pct"}
    assert report.loc[report.criterion=="per_forward_v21","resolution"].iloc[0] == "ALIAS:per_forward_yf"
    assert report.loc[report.criterion=="revenue_growth_v21_pct","availability_pct"].iloc[0] == 50.0

def test_tct_is_shadow_only_and_requires_baseline_before_t1_t2():
    t=tct_adapter().iloc[0]
    assert t["horizon"]=="TCT"
    assert t["status"]=="SHADOW_BASELINE_REQUIRED"
    assert "T1/T2 ACTION TCT only" in t["notes"]

def test_sector_ranking_is_within_sector_and_horizon():
    d=pd.DataFrame([{"sector":"FINANCE","asset_class":"ACTION","horizon":"MT","name":"A","isin":"1","score":80,"decision":"BUY","coverage_pct":100},{"sector":"FINANCE","asset_class":"ACTION","horizon":"MT","name":"B","isin":"2","score":90,"decision":"BUY","coverage_pct":100},{"sector":"SANTE","asset_class":"ACTION","horizon":"MT","name":"C","isin":"3","score":85,"decision":"BUY","coverage_pct":100}])
    r=sector_ranking(d)
    finance=r[(r.sector=="FINANCE") & (r.horizon=="MT")].sort_values("rank")
    assert list(finance["name"])==["B","A"]

def test_quality_gate_uses_canonical_input_count_not_stale_static_threshold():
    actions=pd.DataFrame({"isin":["A1","A2"],"yahoo_ticker":["A1.PA","A2.PA"]})
    etfs=pd.DataFrame({"isin":["E1"],"yahoo_ticker":["E1.PA"]})
    before={"ACTION":{"coverage_pct":100.0},"ETF":{"coverage_pct":100.0}}
    after={"ACTION":{"coverage_pct":100.0},"ETF":{"coverage_pct":100.0}}
    cfg={"quality_gates":{"actions_min_rows":1486,"etf_min_rows":102,"ticker_coverage_min_pct":100.0,"coverage_regression_tolerance_points":0.0,"ohlcv_success_min_pct":90.0}}
    waves={"WAVE_01":{"requested":2,"successful":2},"WAVE_02":{"requested":1,"successful":1}}
    q=run_quality_gates(actions,etfs,before,after,cfg,waves,expected_rows={"ACTION":2,"ETF":1})
    assert q.passed is True
    assert q.checks[0]["threshold"] == 2
    assert q.checks[1]["threshold"] == 1
