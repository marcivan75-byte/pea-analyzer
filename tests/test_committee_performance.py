from pathlib import Path
import json
import pandas as pd

from v182.reporting.committee_performance import run


def test_virtual_book_only_opens_buy_at_or_above_minimum_score(tmp_path: Path):
    (tmp_path/"config").mkdir(); (tmp_path/"outputs"/"committee_master").mkdir(parents=True); (tmp_path/"outputs").mkdir(exist_ok=True)
    cfg={
        "initial_capital_eur":100000.0,"max_total_exposure_pct":80.0,"cash_buffer_min_pct":20.0,
        "max_position_pct":5.0,"max_sector_exposure_pct":20.0,"risk_budget_per_position_pct":0.75,
        "transaction_cost_per_side_pct":0.25,"minimum_buy_score":77.0,
        "stops_pct":{"TCT":6.0,"CT":8.0,"MT":12.0,"LT":18.0},
        "exit_on_decisions":["REJECT","REVIEW","BLOCK_DATA","FAILED"],
        "buy_decisions":["BUY_CANDIDATE"],"gold_in_virtual_pea_book":False,
    }
    (tmp_path/"config"/"COMMITTEE_VIRTUAL_MONEY_MANAGEMENT.json").write_text(json.dumps(cfg),encoding="utf-8")
    decisions=pd.DataFrame([
        {"asset_class":"ACTION","horizon":"CT","isin":"A76","name":"Below","sector":"TECH","score":76.0,"decision":"BUY_CANDIDATE"},
        {"asset_class":"ACTION","horizon":"CT","isin":"A80","name":"Eligible","sector":"TECH","score":80.0,"decision":"BUY_CANDIDATE"},
    ])
    decisions.to_csv(tmp_path/"outputs"/"committee_master"/"COMMITTEE_DECISIONS.csv",sep=";",index=False,encoding="utf-8-sig")
    actions=pd.DataFrame([
        {"isin":"A76","name":"Below","last_close":100.0,"sector_yf":"TECH"},
        {"isin":"A80","name":"Eligible","last_close":100.0,"sector_yf":"TECH"},
    ])
    actions.to_csv(tmp_path/"outputs"/"V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",sep=";",index=False,encoding="utf-8-sig")
    pd.DataFrame(columns=["isin","name"]).to_csv(tmp_path/"outputs"/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",sep=";",index=False,encoding="utf-8-sig")
    result=run(tmp_path)
    assert result["status"]=="SUCCESS"
    assert result["signals"]==1
    assert result["open_positions"]==1
    signals=pd.read_csv(tmp_path/"state"/"committee_performance"/"signals.csv",sep=";")
    assert list(signals["isin"])==["A80"]
    positions=pd.read_csv(tmp_path/"state"/"committee_performance"/"positions.csv",sep=";")
    assert positions.iloc[0]["entry_score"]==80.0
    assert result["nav_eur"] < 100000.0  # transaction cost is charged.
    assert (tmp_path/"outputs"/"performance"/"COMMITTEE_BUY_PERFORMANCE.xlsx").exists()
