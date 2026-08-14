from pathlib import Path
import json
import pandas as pd

from v182.reporting.boursorama_monitor import run


def test_boursorama_monitor_reports_coverage_and_http_blocks(tmp_path: Path):
    (tmp_path / "outputs" / "gaps").mkdir(parents=True)
    (tmp_path / "outputs" / "committee_master").mkdir(parents=True)

    pd.DataFrame([
        {"isin":"E1","boursorama_category_rank_latest":5},
        {"isin":"E2","boursorama_category_rank_latest":None},
    ]).to_csv(tmp_path/"outputs"/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",sep=";",index=False,encoding="utf-8-sig")
    pd.DataFrame([
        {"isin":"E2","source":"Boursorama","reason":"HTTP_429"},
    ]).to_csv(tmp_path/"outputs"/"gaps"/"V21_ETF_BOURSORAMA_RANK_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")
    pd.DataFrame([
        {"isin":"A1","boursorama_consensus_signal":"BUY","postselection_data_status":"AVAILABLE"},
        {"isin":"A2","boursorama_consensus_signal":None,"postselection_data_status":"MISSING"},
    ]).to_csv(tmp_path/"outputs"/"committee_master"/"POSTSELECTION_MARKET_SHEETS.csv",sep=";",index=False,encoding="utf-8-sig")
    pd.DataFrame([
        {"isin":"A2","source":"Boursorama","reason":"FIELDS_NOT_OBSERVED"},
    ]).to_csv(tmp_path/"outputs"/"gaps"/"V21_6_3_POSTSELECTION_MARKET_SHEETS_FAILURES.csv",sep=";",index=False,encoding="utf-8-sig")

    payload=run(tmp_path)
    assert payload["status"] == "WARN"
    assert payload["etf"]["rank_coverage_pct"] == 50.0
    assert payload["actions_postselection"]["boursorama_coverage_pct"] == 50.0
    assert payload["network"]["http_403_or_429"] == 1
    assert (tmp_path/"outputs"/"audit"/"BOURSORAMA_IMPORT_MONITOR.json").exists()
    saved=json.loads((tmp_path/"outputs"/"audit"/"BOURSORAMA_IMPORT_MONITOR.json").read_text())
    assert saved["governance"]["missing_never_imputed"] is True
