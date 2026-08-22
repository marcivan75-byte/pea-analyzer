from __future__ import annotations

import pandas as pd

from v182.core import merge


def test_repeated_merge_timestamps_are_parsed_once_per_unique_text(monkeypatch) -> None:
    merge._parse_as_of_text.cache_clear()
    original=pd.to_datetime
    calls=0

    def counted(*args,**kwargs):
        nonlocal calls
        calls+=1
        return original(*args,**kwargs)

    monkeypatch.setattr(merge.pd,"to_datetime",counted)
    existing={"value":"100","evidence_level":"C","as_of":"2026-08-21T20:00:00+00:00"}
    incoming={
        "value":"101",
        "evidence_level":"C",
        "as_of":"2026-08-22T20:00:00+00:00",
        "validation_status":"AUTO_MATCH",
    }

    for _ in range(200):
        decision=merge.decide(existing,incoming)
        assert decision.action == "REPLACE"
        assert decision.reason == "FRESHER_EQUAL_EVIDENCE"

    assert calls == 2
    info=merge._parse_as_of_text.cache_info()
    assert info.maxsize == 16384
    assert info.hits >= 398
    assert info.misses == 2


def test_numeric_legacy_cells_still_never_reach_pandas_date_parser(monkeypatch) -> None:
    merge._parse_as_of_text.cache_clear()

    def forbidden(*args,**kwargs):
        raise AssertionError("numeric legacy cell must not be parsed as a timestamp")

    monkeypatch.setattr(merge.pd,"to_datetime",forbidden)
    assert merge._as_of_timestamp("64.14") is None
    assert merge._as_of_timestamp("64.14") is None


def test_invalid_timestamp_result_is_cached_without_changing_quarantine_semantics(monkeypatch) -> None:
    merge._parse_as_of_text.cache_clear()
    original=pd.to_datetime
    calls=0

    def counted(*args,**kwargs):
        nonlocal calls
        calls+=1
        return original(*args,**kwargs)

    monkeypatch.setattr(merge.pd,"to_datetime",counted)
    existing={"value":"100","evidence_level":"C","as_of":"2026-08-21"}
    incoming={
        "value":"101",
        "evidence_level":"C",
        "as_of":"not-a-date",
        "validation_status":"AUTO_MATCH",
    }

    for _ in range(50):
        decision=merge.decide(existing,incoming)
        assert decision.action == "QUARANTINE"
        assert decision.reason == "INVALID_FRESHNESS_TIMESTAMP"

    # One valid existing date + one invalid incoming string, each parsed once.
    assert calls == 2
