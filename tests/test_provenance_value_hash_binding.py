from __future__ import annotations

from pathlib import Path
import pandas as pd

from v182.audit.provenance import append_records, load_latest
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
