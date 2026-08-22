from __future__ import annotations

import pandas as pd

from v182.audit import provenance


def _record(*, universe: str, isin: str, field: str, source: str, action: str, value: str, as_of: str) -> dict:
    return {
        "universe": universe,
        "isin": isin,
        "field": field,
        "source": source,
        "source_url": f"https://example.test/{source}",
        "evidence_level": "B",
        "as_of": as_of,
        "validation_status": "VALIDATED",
        "merge_action": action,
        "merge_reason": "TEST",
        "value": value,
    }


def _row(frame: pd.DataFrame, universe: str, field: str) -> pd.Series:
    rows=frame[(frame["universe"].astype(str)==universe) & (frame["field"].astype(str)==field)]
    assert len(rows)==1
    return rows.iloc[0]


def test_retained_replace_refreshes_only_impacted_source_group(tmp_path, monkeypatch):
    path=tmp_path/"OBSERVATION_PROVENANCE.csv"
    provenance.append_records(
        [
            _record(universe="ACTION",isin="FR0001",field="market_cap",source="SRC_A",action="INSERT",value="10",as_of="2026-08-20"),
            _record(universe="ACTION",isin="FR0002",field="market_cap",source="SRC_B",action="INSERT",value="20",as_of="2026-08-21"),
            _record(universe="ETF",isin="LU0001",field="ter_pct",source="SRC_ETF",action="INSERT",value="0.2",as_of="2026-08-19"),
        ],
        path,
    )
    baseline=provenance.actual_sources_by_field(path)
    assert _row(baseline,"ACTION","market_cap")["sources_reelles"]=="SRC_A | SRC_B"
    etf_before=_row(baseline,"ETF","ter_pct").to_dict()

    calls=[]
    original=provenance._aggregate_sources

    def spy(frame):
        calls.append(frame.copy())
        return original(frame)

    monkeypatch.setattr(provenance,"_aggregate_sources",spy)
    provenance.append_records(
        [
            _record(universe="ACTION",isin="FR0001",field="market_cap",source="SRC_C",action="REPLACE",value="11",as_of="2026-08-22"),
        ],
        path,
    )

    assert len(calls)==1
    assert set(calls[0]["universe"].astype(str))=={"ACTION"}
    assert set(calls[0]["field"].astype(str))=={"market_cap"}
    assert set(calls[0]["isin"].astype(str))=={"FR0001","FR0002"}

    after=provenance.actual_sources_by_field(path)
    assert len(calls)==1, "cached aggregate must survive the retained append"
    action=_row(after,"ACTION","market_cap")
    assert action["sources_reelles"]=="SRC_B | SRC_C"
    assert "SRC_A" not in action["sources_reelles"]
    assert action["last_as_of"]=="2026-08-22"
    assert _row(after,"ETF","ter_pct").to_dict()==etf_before


def test_nonretained_append_does_not_refresh_source_aggregate(tmp_path, monkeypatch):
    path=tmp_path/"OBSERVATION_PROVENANCE.csv"
    provenance.append_records(
        [_record(universe="ACTION",isin="FR0001",field="pb",source="SRC_A",action="INSERT",value="1.0",as_of="2026-08-20")],
        path,
    )
    baseline=provenance.actual_sources_by_field(path)

    calls=[]
    original=provenance._aggregate_sources

    def spy(frame):
        calls.append(frame.copy())
        return original(frame)

    monkeypatch.setattr(provenance,"_aggregate_sources",spy)
    provenance.append_records(
        [_record(universe="ACTION",isin="FR0001",field="pb",source="SRC_REJECTED",action="QUARANTINE",value="9.9",as_of="2026-08-22")],
        path,
    )
    after=provenance.actual_sources_by_field(path)
    assert calls==[]
    pd.testing.assert_frame_equal(after,baseline)


def test_incremental_aggregate_matches_full_rebuild(tmp_path):
    path=tmp_path/"OBSERVATION_PROVENANCE.csv"
    provenance.append_records(
        [
            _record(universe="ACTION",isin="FR1",field="f1",source="A",action="INSERT",value="1",as_of="2026-08-18"),
            _record(universe="ACTION",isin="FR2",field="f1",source="B",action="INSERT",value="2",as_of="2026-08-19"),
            _record(universe="ACTION",isin="FR1",field="f2",source="C",action="INSERT",value="3",as_of="2026-08-20"),
            _record(universe="ETF",isin="LU1",field="f1",source="D",action="INSERT",value="4",as_of="2026-08-17"),
        ],
        path,
    )
    provenance.actual_sources_by_field(path)
    provenance.append_records(
        [
            _record(universe="ACTION",isin="FR2",field="f1",source="E",action="REPLACE",value="5",as_of="2026-08-22"),
            _record(universe="ETF",isin="LU1",field="f1",source="F",action="REPLACE",value="6",as_of="2026-08-21"),
        ],
        path,
    )
    incremental=provenance.actual_sources_by_field(path).sort_values(["universe","field"]).reset_index(drop=True)
    retained=provenance._latest_retained_for_path(path)
    rebuilt=provenance._aggregate_sources(retained).sort_values(["universe","field"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(incremental,rebuilt)
