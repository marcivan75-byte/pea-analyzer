from __future__ import annotations

from pathlib import Path
import pandas as pd

from v182.audit.provenance import actual_sources_by_field, append_records, load_latest
from v182.io.frames import apply_observations


def _seed_retained(ledger:Path,value:str,evidence:str="A"):
    append_records([{
        "universe":"ACTION","isin":"A1","field":"x","value":value,"source":"OLD_SOURCE",
        "evidence_level":evidence,"as_of":"2026-08-01","validation_status":"AUTO_MATCH",
        "merge_action":"INSERT","merge_reason":"FIRST_OBSERVATION",
    }],ledger)


def test_stale_provenance_hash_cannot_lend_old_evidence_to_changed_master_value(tmp_path:Path,monkeypatch):
    ledger=tmp_path/"provenance.csv"; monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    _seed_retained(ledger,"10","A")
    frame=pd.DataFrame([{"isin":"A1","x":"20","evidence_level":"D","as_of_date":"2026-08-10"}])
    incoming={"universe":"ACTION","isin":"A1","field":"x","value":"15","source":"NEW_B","evidence_level":"B","as_of":"2026-08-13","validation_status":"AUTO_MATCH"}
    out,q=apply_observations(frame,[incoming])
    assert not q
    assert out.loc[0,"x"]=="15"
    ledger_df=pd.read_csv(ledger,sep=";",dtype=str)
    assert ledger_df.iloc[-1]["merge_action"]=="REPLACE"
    assert ledger_df.iloc[-1]["merge_reason"]=="HIGHER_EVIDENCE"


def test_matching_provenance_hash_keeps_stronger_retained_evidence(tmp_path:Path,monkeypatch):
    ledger=tmp_path/"provenance.csv"; monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    _seed_retained(ledger,"10","A")
    frame=pd.DataFrame([{"isin":"A1","x":"10","evidence_level":"D","as_of_date":"2026-08-10"}])
    incoming={"universe":"ACTION","isin":"A1","field":"x","value":"15","source":"NEW_B","evidence_level":"B","as_of":"2026-08-13","validation_status":"AUTO_MATCH"}
    out,q=apply_observations(frame,[incoming])
    assert not q
    assert out.loc[0,"x"]=="10"
    ledger_df=pd.read_csv(ledger,sep=";",dtype=str)
    assert ledger_df.iloc[-1]["merge_action"]=="KEEP"
    latest=load_latest(ledger)
    assert latest[("A1","x")]["source"]=="OLD_SOURCE"


def test_in_memory_replacement_keeps_hash_bound_provenance_within_same_batch(tmp_path:Path,monkeypatch):
    ledger=tmp_path/"provenance.csv"; monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    frame=pd.DataFrame([{"isin":"A1","x":pd.NA,"evidence_level":"D","as_of_date":"2026-08-01"}])
    observations=[
        {"universe":"ACTION","isin":"A1","field":"x","value":"10","source":"C","evidence_level":"C","as_of":"2026-08-12","validation_status":"AUTO_MATCH"},
        {"universe":"ACTION","isin":"A1","field":"x","value":"20","source":"B","evidence_level":"B","as_of":"2026-08-13","validation_status":"AUTO_MATCH"},
    ]
    out,q=apply_observations(frame,observations)
    assert not q
    assert out.loc[0,"x"]=="20"
    ledger_df=pd.read_csv(ledger,sep=";",dtype=str)
    assert list(ledger_df["merge_action"][-2:])==["INSERT","REPLACE"]


def test_actual_sources_report_only_latest_retained_source_per_key(tmp_path:Path):
    ledger=tmp_path/"provenance.csv"
    append_records([
        {"universe":"ACTION","isin":"A1","field":"x","value":"10","source":"SOURCE_A","source_url":"a","evidence_level":"C","as_of":"2026-08-01","validation_status":"AUTO_MATCH","merge_action":"INSERT","merge_reason":"FIRST"},
    ],ledger)
    append_records([
        {"universe":"ACTION","isin":"A1","field":"x","value":"20","source":"SOURCE_B","source_url":"b","evidence_level":"B","as_of":"2026-08-02","validation_status":"AUTO_MATCH","merge_action":"REPLACE","merge_reason":"HIGHER"},
        {"universe":"ACTION","isin":"A2","field":"x","value":"30","source":"SOURCE_C","source_url":"c","evidence_level":"B","as_of":"2026-08-02","validation_status":"AUTO_MATCH","merge_action":"INSERT","merge_reason":"FIRST"},
    ],ledger)
    sources=actual_sources_by_field(ledger)
    row=sources[(sources["universe"]=="ACTION")&(sources["field"]=="x")].iloc[0]
    assert row["sources_reelles"]=="SOURCE_B | SOURCE_C"
    assert "SOURCE_A" not in row["sources_reelles"]


def test_legacy_row_b_does_not_freeze_fresh_ohlcv_field(tmp_path:Path,monkeypatch):
    ledger=tmp_path/"provenance.csv"; monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    frame=pd.DataFrame([{
        "isin":"A1","rsi14":"40.0","evidence_level":"B","ta_as_of":"2026-08-05T09:00:00","as_of_date":"2026-08-05",
    }])
    incoming={"universe":"ACTION","isin":"A1","field":"rsi14","value":55.0,"source":"INTERNAL_FROM_OHLCV","evidence_level":"C","as_of":"2026-08-13","validation_status":"AUTO_MATCH"}
    out,q=apply_observations(frame,[incoming])
    assert not q
    assert out.loc[0,"rsi14"]=="55.0"
    record=pd.read_csv(ledger,sep=";",dtype=str).iloc[-1]
    assert record["merge_action"]=="REPLACE"
    assert record["merge_reason"]=="FRESHER_EQUAL_EVIDENCE"


def test_legacy_row_a_still_blocks_lower_ohlcv_evidence(tmp_path:Path,monkeypatch):
    ledger=tmp_path/"provenance.csv"; monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    frame=pd.DataFrame([{
        "isin":"A1","rsi14":"40.0","evidence_level":"A","ta_as_of":"2026-08-05T09:00:00","as_of_date":"2026-08-05",
    }])
    incoming={"universe":"ACTION","isin":"A1","field":"rsi14","value":55.0,"source":"INTERNAL_FROM_OHLCV","evidence_level":"C","as_of":"2026-08-13","validation_status":"AUTO_MATCH"}
    out,q=apply_observations(frame,[incoming])
    assert not q
    assert out.loc[0,"rsi14"]=="40.0"
    record=pd.read_csv(ledger,sep=";",dtype=str).iloc[-1]
    assert record["merge_action"]=="KEEP"
    assert record["merge_reason"]=="LOWER_EVIDENCE"


def test_unrelated_legacy_row_b_field_is_not_demoted(tmp_path:Path,monkeypatch):
    ledger=tmp_path/"provenance.csv"; monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    frame=pd.DataFrame([{"isin":"A1","custom_manual_field":"10","evidence_level":"B","as_of_date":"2026-08-05"}])
    incoming={"universe":"ACTION","isin":"A1","field":"custom_manual_field","value":"20","source":"OTHER_C","evidence_level":"C","as_of":"2026-08-13","validation_status":"AUTO_MATCH"}
    out,q=apply_observations(frame,[incoming])
    assert not q
    assert out.loc[0,"custom_manual_field"]=="10"
    assert pd.read_csv(ledger,sep=";",dtype=str).iloc[-1]["merge_reason"]=="LOWER_EVIDENCE"


def test_explicit_yfinance_field_bootstraps_as_c_and_refreshes(tmp_path:Path,monkeypatch):
    ledger=tmp_path/"provenance.csv"; monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    frame=pd.DataFrame([{
        "isin":"A1","per_forward_yf":"11.0","evidence_level":"B","yf_consensus_as_of":"2026-08-05","as_of_date":"2026-08-05",
    }])
    incoming={"universe":"ACTION","isin":"A1","field":"per_forward_yf","value":12.0,"source":"yfinance","evidence_level":"C","as_of":"2026-08-13","validation_status":"AUTO_MATCH"}
    out,q=apply_observations(frame,[incoming])
    assert not q
    assert out.loc[0,"per_forward_yf"]=="12.0"
    assert pd.read_csv(ledger,sep=";",dtype=str).iloc[-1]["merge_reason"]=="FRESHER_EQUAL_EVIDENCE"


def test_generic_yfinance_field_requires_legacy_yfinance_marker(tmp_path:Path,monkeypatch):
    ledger=tmp_path/"provenance.csv"; monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    frame=pd.DataFrame([
        {"isin":"A1","beta":"0.8","evidence_level":"B","fundamentals_source":"yfinance","fundamentals_as_of":"2026-08-05","as_of_date":"2026-08-05"},
        {"isin":"A2","beta":"0.9","evidence_level":"B","fundamentals_source":"Issuer","fundamentals_as_of":"2026-08-05","as_of_date":"2026-08-05"},
    ])
    observations=[
        {"universe":"ACTION","isin":"A1","field":"beta","value":1.1,"source":"yfinance","evidence_level":"C","as_of":"2026-08-13","validation_status":"AUTO_MATCH"},
        {"universe":"ACTION","isin":"A2","field":"beta","value":1.2,"source":"yfinance","evidence_level":"C","as_of":"2026-08-13","validation_status":"AUTO_MATCH"},
    ]
    out,q=apply_observations(frame,observations)
    assert not q
    by=out.set_index("isin")
    assert by.at["A1","beta"]=="1.1"
    assert by.at["A2","beta"]=="0.9"
    ledger_df=pd.read_csv(ledger,sep=";",dtype=str)
    assert list(ledger_df["merge_action"][-2:])==["REPLACE","KEEP"]
