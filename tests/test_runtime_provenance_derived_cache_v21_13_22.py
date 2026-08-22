from __future__ import annotations

import pandas as pd

from v182.audit import provenance


def _record(
    *,
    value: str,
    source: str = "SOURCE_A",
    action: str = "INSERT",
    field: str = "test_field",
) -> dict:
    return {
        "universe":"ACTION",
        "isin":"FR0000000001",
        "field":field,
        "value":value,
        "source":source,
        "source_url":"",
        "evidence_level":"C",
        "as_of":"2026-08-22",
        "validation_status":"AUTO_MATCH",
        "merge_action":action,
        "merge_reason":"TEST",
    }


def test_latest_mapping_is_built_once_and_public_results_are_isolated(tmp_path,monkeypatch) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_record(value="1")],path=ledger)

    original=provenance._latest_mapping
    builds=0

    def counted(latest):
        nonlocal builds
        builds+=1
        return original(latest)

    monkeypatch.setattr(provenance,"_latest_mapping",counted)

    first=provenance.load_latest(ledger)
    key=("FR0000000001","test_field")
    first[key]["source"]="MUTATED_BY_CALLER"
    first[("FAKE","FIELD")]={"source":"FAKE"}

    second=provenance.load_latest(ledger)

    assert builds == 1
    assert second[key]["source"] == "SOURCE_A"
    assert ("FAKE","FIELD") not in second


def test_source_aggregate_survives_nonretained_append_but_refreshes_on_replace(tmp_path,monkeypatch) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_record(value="1",source="SOURCE_A")],path=ledger)

    original=provenance._aggregate_sources
    builds=0

    def counted(retained):
        nonlocal builds
        builds+=1
        return original(retained)

    monkeypatch.setattr(provenance,"_aggregate_sources",counted)

    first=provenance.actual_sources_by_field(ledger)
    second=provenance.actual_sources_by_field(ledger)
    assert builds == 1
    assert first.loc[0,"sources_reelles"] == "SOURCE_A"
    assert second.loc[0,"sources_reelles"] == "SOURCE_A"

    provenance.append_records([_record(value="1",source="IGNORED_KEEP",action="KEEP")],path=ledger)
    after_keep=provenance.actual_sources_by_field(ledger)
    assert builds == 1
    assert after_keep.loc[0,"sources_reelles"] == "SOURCE_A"

    provenance.append_records([_record(value="2",source="SOURCE_B",action="REPLACE")],path=ledger)
    after_replace=provenance.actual_sources_by_field(ledger)
    assert builds == 2
    assert after_replace.loc[0,"sources_reelles"] == "SOURCE_B"


def test_cached_source_aggregate_return_is_independent(tmp_path) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_record(value="1",source="SOURCE_A")],path=ledger)

    first=provenance.actual_sources_by_field(ledger)
    first.loc[0,"sources_reelles"]="MUTATED_BY_CALLER"
    second=provenance.actual_sources_by_field(ledger)

    assert second.loc[0,"sources_reelles"] == "SOURCE_A"


def test_external_change_rebuilds_derived_mapping_and_sources(tmp_path,monkeypatch) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_record(value="1",source="SOURCE_A")],path=ledger)
    provenance.load_latest(ledger)
    provenance.actual_sources_by_field(ledger)

    original_mapping=provenance._latest_mapping
    original_aggregate=provenance._aggregate_sources
    mapping_builds=0
    aggregate_builds=0

    def counted_mapping(latest):
        nonlocal mapping_builds
        mapping_builds+=1
        return original_mapping(latest)

    def counted_aggregate(retained):
        nonlocal aggregate_builds
        aggregate_builds+=1
        return original_aggregate(retained)

    monkeypatch.setattr(provenance,"_latest_mapping",counted_mapping)
    monkeypatch.setattr(provenance,"_aggregate_sources",counted_aggregate)

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
    sources=provenance.actual_sources_by_field(ledger)

    assert mapping_builds == 1
    assert aggregate_builds == 1
    assert latest[("FR0000000001","test_field")]["source"] == "EXTERNAL_SOURCE"
    assert sources.loc[0,"sources_reelles"] == "EXTERNAL_SOURCE"
