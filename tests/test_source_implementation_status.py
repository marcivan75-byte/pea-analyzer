from __future__ import annotations

from pathlib import Path
import pandas as pd


def test_source_implementation_status_is_explicit_and_active_paths_exist():
    root=Path(__file__).resolve().parents[1]
    status=pd.read_csv(root/"config"/"V21_SOURCE_IMPLEMENTATION_STATUS.csv",sep=";",dtype=str).fillna("")
    required={"Source","Implementation_status","Active_code_path","Governance_note"}
    assert required.issubset(status.columns)
    assert status["Source"].is_unique
    assert not (status["Implementation_status"].str.strip()=="").any()

    for _,row in status.iterrows():
        implementation=str(row["Implementation_status"])
        paths=[p.strip() for p in str(row["Active_code_path"]).split("|") if p.strip()]
        if implementation.startswith("ACTIVE_"):
            assert paths, f"Active source has no auditable code path: {row['Source']}"
            for path in paths:
                assert (root/path).exists(), f"Missing active source path {path} for {row['Source']}"


def test_declared_but_unwired_sources_are_not_misrepresented_as_active():
    root=Path(__file__).resolve().parents[1]
    status=pd.read_csv(root/"config"/"V21_SOURCE_IMPLEMENTATION_STATUS.csv",sep=";",dtype=str).fillna("").set_index("Source")
    assert status.loc["Marketstack","Implementation_status"]=="DECLARED_NOT_WIRED"
    assert status.loc["Alpha Vantage","Implementation_status"]=="DECLARED_NOT_WIRED"
    assert status.loc["EIA","Implementation_status"]=="DECLARED_NOT_WIRED"
    assert status.loc["Finnhub","Implementation_status"]=="ACTIVE_AUTOMATED"
    assert status.loc["FRED","Implementation_status"]=="ACTIVE_AUTOMATED"
    assert status.loc["OpenFIGI","Implementation_status"]=="ACTIVE_AUTOMATED"


def test_boursorama_is_high_priority_attributed_not_automated_scraping():
    root=Path(__file__).resolve().parents[1]
    status=pd.read_csv(root/"config"/"V21_SOURCE_IMPLEMENTATION_STATUS.csv",sep=";",dtype=str).fillna("").set_index("Source")
    row=status.loc["Boursorama"]
    assert row["Implementation_status"]=="ACTIVE_ATTRIBUTED_IMPORT_HIGH_PRIORITY"
    for module in (
        "boursorama_import.py",
        "boursorama_bulk_import.py",
        "boursorama_current_summary.py",
        "boursorama_consensus_depth.py",
        "boursorama_action_extended.py",
        "boursorama_profile_currency_guard.py",
        "boursorama_company_calendar.py",
        "boursorama_etf_import.py",
        "unified_runner.py",
    ):
        assert module in row["Active_code_path"]
    note=row["Governance_note"].lower()
    assert "no direct automated recovery" in note or "direct automated recovery is not performed" in note
    assert "context/shadow" in note
    assert "market cap/dividend" in note
    assert "local currencies remain reported context" in note
    assert "management fee maximum is never relabelled ter" in note
