from __future__ import annotations

import pandas as pd

from v182.io import frames


def _incoming(field: str, value, *, as_of: str = "2026-08-23", source: str = "YFINANCE") -> dict:
    return {
        "universe":"ACTION",
        "isin":"FR0000000001",
        "field":field,
        "value":value,
        "source":source,
        "evidence_level":"C",
        "as_of":as_of,
        "validation_status":"AUTO_MATCH",
    }


def test_many_legacy_fields_build_row_context_once_per_isin(tmp_path,monkeypatch) -> None:
    monkeypatch.setenv("PEA_PROVENANCE_PATH",str(tmp_path/"provenance.csv"))
    fields=[
        "per_ttm_yf","per_forward_yf","revenue_growth_yf","earnings_growth_yf",
        "target_mean_yf","target_high_yf","target_low_yf","n_analysts_yf",
        "recommendation_mean_yf","dividend_rate_yf",
    ]
    data={
        "isin":["FR0000000001"],
        "name":["Test"],
        "evidence_level":["C"],
        "as_of_date":["2026-08-20"],
        "fundamentals_as_of":["2026-08-21"],
        "yf_consensus_as_of":["2026-08-21"],
        "fundamentals_source":["yfinance"],
    }
    for idx,field in enumerate(fields):
        data[field]=[str(idx+1)]
    frame=pd.DataFrame(data)
    observations=[_incoming(field,str(idx+101)) for idx,field in enumerate(fields)]

    original=frames._legacy_row_context
    builds=0

    def counted(frame_arg,isin_arg):
        nonlocal builds
        builds+=1
        return original(frame_arg,isin_arg)

    monkeypatch.setattr(frames,"_legacy_row_context",counted)
    out,quarantine=frames.apply_observations(frame,observations)

    assert builds == 1
    assert quarantine == []
    for idx,field in enumerate(fields):
        assert out.loc[0,field] == str(idx+101)


def test_context_is_invalidated_when_same_batch_changes_freshness_input(tmp_path,monkeypatch) -> None:
    monkeypatch.setenv("PEA_PROVENANCE_PATH",str(tmp_path/"provenance.csv"))
    frame=pd.DataFrame({
        "isin":["FR0000000001"],
        "name":["Test"],
        "evidence_level":["C"],
        "as_of_date":["2026-08-20"],
        "fundamentals_as_of":["2026-08-21"],
        "fundamentals_source":["yfinance"],
        "market_cap":["100"],
    })
    observations=[
        _incoming("fundamentals_as_of","2026-08-23",as_of="2026-08-23",source="TEST"),
        _incoming("market_cap","200",as_of="2026-08-22",source="YFINANCE"),
    ]

    original=frames._legacy_row_context
    builds=0

    def counted(frame_arg,isin_arg):
        nonlocal builds
        builds+=1
        return original(frame_arg,isin_arg)

    monkeypatch.setattr(frames,"_legacy_row_context",counted)
    out,quarantine=frames.apply_observations(frame,observations)

    # One context for the freshness-field merge, then a fresh context after that
    # field is replaced. The market-cap observation is now older than the updated
    # fundamentals timestamp and must not overwrite the legacy value.
    assert builds == 2
    assert out.loc[0,"fundamentals_as_of"] == "2026-08-23"
    assert out.loc[0,"market_cap"] == "100"
    assert any(item.get("field")=="market_cap" and item.get("reason")=="CONFLICT_EQUAL_EVIDENCE" for item in quarantine)


def test_standalone_legacy_metadata_matches_cached_context_path() -> None:
    frame=pd.DataFrame({
        "isin":["FR0000000001"],
        "name":["Test"],
        "evidence_level":["C"],
        "as_of_date":["2026-08-20"],
        "ta_as_of":["2026-08-21"],
        "perf_as_of":["2026-08-19"],
        "fundamentals_as_of":["2026-08-22"],
        "yf_consensus_as_of":["2026-08-21"],
        "fundamentals_source":["yfinance"],
        "market_cap":["100"],
    }).set_index("isin",drop=False)
    isin="FR0000000001"
    incoming=_incoming("market_cap","200")
    context=frames._legacy_row_context(frame,isin)

    direct=frames._legacy_field_metadata(frame,isin,"market_cap",incoming)
    cached=frames._legacy_field_metadata(frame,isin,"market_cap",incoming,context=context)

    assert direct == cached == {
        "evidence_level":"C",
        "as_of":"2026-08-22",
        "bootstrap":"LEGACY_YFINANCE_MARKED_C",
    }
