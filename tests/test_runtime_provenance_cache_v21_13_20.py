from __future__ import annotations

import warnings

import pandas as pd
from pandas.errors import PerformanceWarning

from v182.audit import provenance
from v182.io import frames


def _record(*, value: str, source: str = "SOURCE_A", action: str = "INSERT") -> dict:
    return {
        "universe": "ACTION",
        "isin": "FR0000000001",
        "field": "test_field",
        "value": value,
        "source": source,
        "source_url": "",
        "evidence_level": "C",
        "as_of": "2026-08-22",
        "validation_status": "AUTO_MATCH",
        "merge_action": action,
        "merge_reason": "TEST",
    }


def test_provenance_latest_cache_reused_and_updated_after_owned_append(tmp_path, monkeypatch) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_record(value="1")],path=ledger)

    original_read=provenance._read_ledger
    reads=0

    def counted_read(path):
        nonlocal reads
        reads+=1
        return original_read(path)

    monkeypatch.setattr(provenance,"_read_ledger",counted_read)

    first=provenance.load_latest(ledger)
    second=provenance.load_latest(ledger)
    sources_before=provenance.actual_sources_by_field(ledger)

    assert reads == 1
    assert first[("FR0000000001","test_field")]["source"] == "SOURCE_A"
    assert second[("FR0000000001","test_field")]["source"] == "SOURCE_A"
    assert sources_before.loc[0,"sources_reelles"] == "SOURCE_A"

    provenance.append_records([_record(value="2",source="SOURCE_B",action="REPLACE")],path=ledger)
    third=provenance.load_latest(ledger)
    sources_after=provenance.actual_sources_by_field(ledger)

    assert reads == 1
    assert third[("FR0000000001","test_field")]["source"] == "SOURCE_B"
    assert sources_after.loc[0,"sources_reelles"] == "SOURCE_B"


def test_provenance_cache_invalidates_on_external_file_change(tmp_path, monkeypatch) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_record(value="1")],path=ledger)
    provenance.load_latest(ledger)

    original_read=provenance._read_ledger
    reads=0

    def counted_read(path):
        nonlocal reads
        reads+=1
        return original_read(path)

    monkeypatch.setattr(provenance,"_read_ledger",counted_read)

    external=pd.DataFrame([
        {
            "recorded_at_utc":"2099-01-01T00:00:00+00:00",
            "universe":"ACTION",
            "isin":"FR0000000001",
            "field":"test_field",
            "source":"EXTERNAL_SOURCE",
            "source_url":"",
            "evidence_level":"B",
            "as_of":"2099-01-01",
            "validation_status":"ATTRIBUTED",
            "merge_action":"REPLACE",
            "merge_reason":"EXTERNAL_TEST",
            "value_sha256":provenance.value_hash("3"),
        }
    ],columns=provenance.COLUMNS)
    external.to_csv(ledger,sep=";",encoding="utf-8-sig",index=False,mode="a",header=False)

    latest=provenance.load_latest(ledger)
    assert reads == 1
    assert latest[("FR0000000001","test_field")]["source"] == "EXTERNAL_SOURCE"


def test_empty_observation_merge_does_not_load_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        provenance,
        "load_latest",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ledger must not be read")),
    )
    frame=pd.DataFrame({"isin":["FR0000000001"],"name":["Test"]},index=[7])
    out,quarantine=frames.apply_observations(frame,[])
    assert quarantine == []
    assert list(out.index) == [0]
    assert out.loc[0,"isin"] == "FR0000000001"


def test_missing_observation_columns_are_materialized_without_fragmentation_warning() -> None:
    frame=pd.DataFrame({"isin":["FR0000000001"],"name":["Test"]}).set_index("isin",drop=False)
    observations=[
        {"isin":"FR0000000001","field":f"new_field_{idx}","value":idx}
        for idx in range(160)
    ]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always",PerformanceWarning)
        out=frames._materialize_missing_observation_fields(frame,observations)
    assert not [item for item in caught if issubclass(item.category,PerformanceWarning)]
    assert all(f"new_field_{idx}" in out.columns for idx in range(160))
    assert all(pd.isna(out.at["FR0000000001",f"new_field_{idx}"]) for idx in range(160))
