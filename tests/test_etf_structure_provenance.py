from __future__ import annotations

from pathlib import Path
import pandas as pd

import v182.reporting.etf_structure_refresh as refresh


def test_merge_ready_etf_structure_observations_are_explicitly_evidence_c():
    raw=[{"ticker":"ETF.PA","field":"direct_sector_hhi","value":0.25,"source":"yfinance.funds_data"}]
    out=refresh._merge_ready_observations(raw,{"ETF.PA":"FR000ETF0001"},"2026-08-13")
    assert out==[{
        "universe":"ETF","isin":"FR000ETF0001","field":"direct_sector_hhi","value":0.25,
        "source":"yfinance.funds_data","source_url":"","evidence_level":"C","as_of":"2026-08-13",
        "validation_status":"AUTO_MATCH",
    }]


def test_etf_structure_refresh_cannot_overwrite_stronger_retained_value(tmp_path:Path,monkeypatch):
    root=tmp_path
    outputs=root/"outputs"; outputs.mkdir()
    master=pd.DataFrame([{
        "isin":"FR000ETF0001","name":"ETF","yahoo_ticker":"ETF.PA",
        "direct_sector_hhi":"0.12","diversification_direct_score":pd.NA,
        "evidence_level":"A","as_of_date":"2026-08-12",
    }])
    master.to_csv(outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",sep=";",encoding="utf-8-sig",index=False)
    monkeypatch.setenv("PEA_PROVENANCE_PATH",str(root/"state"/"provenance.csv"))
    monkeypatch.setattr(refresh,"collect_fund_structure",lambda tickers: ([
        {"ticker":"ETF.PA","field":"direct_sector_hhi","value":0.55,"source":"yfinance.funds_data"},
        {"ticker":"ETF.PA","field":"diversification_direct_score","value":45.0,"source":"yfinance.funds_data"},
    ],[]))

    payload=refresh.run(root)
    result=pd.read_csv(outputs/"V18.2_PEA_ETF_MASTER_ENRICHED.csv",sep=";",encoding="utf-8-sig",dtype=str)
    assert result.loc[0,"direct_sector_hhi"]=="0.12"
    assert float(result.loc[0,"diversification_direct_score"])==45.0
    assert payload["changed_cells"]==1
    assert payload["governance"]["provenance_merge_enabled"] is True

    ledger=pd.read_csv(root/"state"/"provenance.csv",sep=";",encoding="utf-8-sig",dtype=str)
    actions=dict(zip(ledger["field"],ledger["merge_action"]))
    assert actions["direct_sector_hhi"]=="KEEP"
    assert actions["diversification_direct_score"]=="INSERT"
