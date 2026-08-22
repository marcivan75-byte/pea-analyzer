from __future__ import annotations

import pandas as pd

from v182.audit import provenance


def _event(*,value:str,source:str,action:str,as_of:str="2026-08-22") -> dict:
    return {
        "universe":"ACTION",
        "isin":"FR0000000001",
        "field":"test_field",
        "value":value,
        "source":source,
        "source_url":"",
        "evidence_level":"C",
        "as_of":as_of,
        "validation_status":"AUTO_MATCH",
        "merge_action":action,
        "merge_reason":"TEST",
    }


def test_latest_event_view_preserves_nonretained_ci_semantics(tmp_path) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_event(value="1",source="INSERT_SOURCE",action="INSERT")],path=ledger)
    # Warm the process cache before the non-retained event is appended.
    assert provenance.load_latest_readonly(ledger)[("FR0000000001","test_field")]["source"] == "INSERT_SOURCE"

    provenance.append_records([_event(value="1",source="KEEP_SOURCE",action="KEEP",as_of="2026-08-23")],path=ledger)

    retained=provenance.load_latest_readonly(ledger)
    latest_event=provenance.load_latest_events_readonly(ledger)
    assert retained[("FR0000000001","test_field")]["source"] == "INSERT_SOURCE"
    assert latest_event[("FR0000000001","test_field")]["source"] == "KEEP_SOURCE"
    assert latest_event[("FR0000000001","test_field")]["merge_action"] == "KEEP"


def test_latest_event_cache_avoids_second_disk_read_and_tracks_owned_append(tmp_path,monkeypatch) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_event(value="1",source="A",action="INSERT")],path=ledger)

    original=provenance._read_ledger
    reads=0

    def counted(path):
        nonlocal reads
        reads+=1
        return original(path)

    monkeypatch.setattr(provenance,"_read_ledger",counted)
    first=provenance.load_latest_events_readonly(ledger)
    second=provenance.load_latest_events_readonly(ledger)
    assert reads == 1
    assert first[("FR0000000001","test_field")]["source"] == "A"
    assert second[("FR0000000001","test_field")]["source"] == "A"

    provenance.append_records([_event(value="1",source="B",action="QUARANTINE",as_of="2026-08-23")],path=ledger)
    third=provenance.load_latest_events_readonly(ledger)
    assert reads == 1
    assert third[("FR0000000001","test_field")]["source"] == "B"
    assert third[("FR0000000001","test_field")]["merge_action"] == "QUARANTINE"


def test_external_file_change_invalidates_latest_event_cache(tmp_path,monkeypatch) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_event(value="1",source="A",action="INSERT")],path=ledger)
    provenance.load_latest_events_readonly(ledger)

    original=provenance._read_ledger
    reads=0

    def counted(path):
        nonlocal reads
        reads+=1
        return original(path)

    monkeypatch.setattr(provenance,"_read_ledger",counted)
    external=pd.DataFrame([{
        "recorded_at_utc":"2099-01-01T00:00:00+00:00",
        "universe":"ACTION",
        "isin":"FR0000000001",
        "field":"test_field",
        "source":"EXTERNAL",
        "source_url":"",
        "evidence_level":"B",
        "as_of":"2099-01-01",
        "validation_status":"ATTRIBUTED",
        "merge_action":"KEEP",
        "merge_reason":"EXTERNAL_TEST",
        "value_sha256":provenance.value_hash("1"),
    }],columns=provenance.COLUMNS)
    external.to_csv(ledger,sep=";",encoding="utf-8-sig",index=False,mode="a",header=False)

    latest=provenance.load_latest_events_readonly(ledger)
    assert reads == 1
    assert latest[("FR0000000001","test_field")]["source"] == "EXTERNAL"


def test_cold_latest_event_mapping_matches_historical_sort_drop_duplicates_semantics(tmp_path) -> None:
    ledger=tmp_path/"provenance.csv"
    rows=pd.DataFrame([
        {
            "recorded_at_utc":"2026-08-23T00:00:00+00:00","universe":"ACTION","isin":"FR0000000001","field":"test_field",
            "source":"NEWER","source_url":"","evidence_level":"C","as_of":"2026-08-23","validation_status":"AUTO_MATCH",
            "merge_action":"KEEP","merge_reason":"TEST","value_sha256":provenance.value_hash("1"),
        },
        {
            "recorded_at_utc":"2026-08-22T00:00:00+00:00","universe":"ACTION","isin":"FR0000000001","field":"test_field",
            "source":"OLDER_APPENDED_LATER","source_url":"","evidence_level":"C","as_of":"2026-08-22","validation_status":"AUTO_MATCH",
            "merge_action":"INSERT","merge_reason":"TEST","value_sha256":provenance.value_hash("1"),
        },
    ],columns=provenance.COLUMNS)
    rows.to_csv(ledger,sep=";",encoding="utf-8-sig",index=False)

    latest=provenance.load_latest_events_readonly(ledger)
    assert latest[("FR0000000001","test_field")]["source"] == "NEWER"
