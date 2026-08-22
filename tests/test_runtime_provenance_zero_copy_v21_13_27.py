from __future__ import annotations

from types import MappingProxyType

import pandas as pd
import pytest

from v182.audit import provenance
from v182.io import frames


def _record(*,value:str,source:str="SOURCE_A",action:str="INSERT",as_of:str="2026-08-22") -> dict:
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


def test_readonly_latest_view_is_zero_copy_outer_mapping_but_public_load_is_isolated(tmp_path) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_record(value="1")],path=ledger)

    view=provenance.load_latest_readonly(ledger)
    assert isinstance(view,MappingProxyType)
    key=("FR0000000001","test_field")
    assert view[key]["source"] == "SOURCE_A"
    with pytest.raises(TypeError):
        view[key]={"source":"MUTATED"}

    public=provenance.load_latest(ledger)
    public[key]["source"]="CALLER_MUTATION"
    assert provenance.load_latest(ledger)[key]["source"] == "SOURCE_A"


def test_retained_dataframe_stays_lazy_after_incremental_source_refresh(tmp_path) -> None:
    ledger=tmp_path/"provenance.csv"
    provenance.append_records([_record(value="1")],path=ledger)
    provenance.actual_sources_by_field(ledger)
    key=provenance._cache_key(ledger)
    before=provenance._LATEST_RETAINED_CACHE[key]
    assert before.latest is not None

    provenance.append_records([_record(value="2",source="SOURCE_B",action="REPLACE",as_of="2026-08-23")],path=ledger)
    after=provenance._LATEST_RETAINED_CACHE[key]
    assert after.latest is None
    assert provenance.load_latest_readonly(ledger)[("FR0000000001","test_field")]["source"] == "SOURCE_B"
    # A map lookup must not force DataFrame materialization.
    assert provenance._LATEST_RETAINED_CACHE[key].latest is None

    sources=provenance.actual_sources_by_field(ledger)
    assert sources.loc[0,"sources_reelles"] == "SOURCE_B"
    # V21.13.33 keeps the retained frame lazy because the exact source aggregate
    # was refreshed only for the impacted field during append_records().
    assert provenance._LATEST_RETAINED_CACHE[key].latest is None


def test_apply_observations_uses_local_overlay_for_repeated_key_without_copying_public_map(tmp_path,monkeypatch) -> None:
    ledger=tmp_path/"provenance.csv"
    monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    monkeypatch.setattr(
        provenance,
        "load_latest",
        lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("public copying loader must not be used")),
    )
    frame=pd.DataFrame({"isin":["FR0000000001"],"name":["Test"]})
    observations=[
        {
            "universe":"ACTION","isin":"FR0000000001","field":"test_field","value":"1",
            "source":"TEST","evidence_level":"C","as_of":"2026-08-22","validation_status":"AUTO_MATCH",
        },
        {
            "universe":"ACTION","isin":"FR0000000001","field":"test_field","value":"2",
            "source":"TEST","evidence_level":"C","as_of":"2026-08-23","validation_status":"AUTO_MATCH",
        },
    ]

    out,quarantine=frames.apply_observations(frame,observations)
    assert quarantine == []
    assert out.loc[0,"test_field"] == "2"
    latest=provenance.load_latest_readonly(ledger)
    assert latest[("FR0000000001","test_field")]["value_sha256"] == provenance.value_hash("2")


def test_reconstructed_retained_frame_preserves_separate_universe_rows(tmp_path) -> None:
    ledger=tmp_path/"provenance.csv"
    records=[
        _record(value="1",source="ACTION_SOURCE"),
        {
            **_record(value="2",source="ETF_SOURCE"),
            "universe":"ETF",
        },
    ]
    provenance.append_records(records,path=ledger)
    provenance.load_latest_readonly(ledger)
    key=provenance._cache_key(ledger)
    # Force the lazy path explicitly.
    provenance._LATEST_RETAINED_CACHE[key].latest=None
    retained=provenance._latest_retained_for_path(ledger)
    assert set(retained["universe"].astype(str)) == {"ACTION","ETF"}
    assert len(retained) == 2
