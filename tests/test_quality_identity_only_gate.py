from __future__ import annotations

import pandas as pd

from v182.audit.quality import run_quality_gates


def _cfg():
    return {
        "quality_gates": {
            "actions_min_rows": 3,
            "etf_min_rows": 1,
            "ticker_coverage_min_pct": 100.0,
            "coverage_regression_tolerance_points": 0.0,
            "ohlcv_success_min_pct": 90.0,
        }
    }


def _frames():
    actions=pd.DataFrame([
        {"isin":"A1","yahoo_ticker":"A1.PA","canonical_seed_status":"LEGACY_ROW"},
        {"isin":"A2","yahoo_ticker":"A2.PA","canonical_seed_status":"LEGACY_ROW"},
        {"isin":"A3","yahoo_ticker":pd.NA,"canonical_seed_status":"WHITELIST_ONLY_MISSING_METADATA"},
    ])
    etf=pd.DataFrame([{"isin":"E1","yahoo_ticker":"E1.PA"}])
    return actions,etf


def _run(actions,etf):
    before={"ACTION":{"coverage_pct":50.0},"ETF":{"coverage_pct":50.0}}
    after={"ACTION":{"coverage_pct":50.0},"ETF":{"coverage_pct":50.0}}
    waves={"WAVE_01":{"requested":2,"successful":2},"WAVE_02":{"requested":1,"successful":1}}
    return run_quality_gates(actions,etf,before,after,_cfg(),waves,expected_rows={"ACTION":3,"ETF":1})


def test_identity_only_rows_do_not_weaken_strict_ticker_gate():
    actions,etf=_frames()
    result=_run(actions,etf)
    assert result.passed
    checks={c["check"]:c for c in result.checks}
    assert checks["actions_ticker_coverage_eligible_pct"]["value"]==100.0
    assert checks["actions_missing_ticker_explicitly_identity_only_pct"]["value"]==100.0
    assert checks["actions_identity_only_rows_have_no_invented_ticker"]["value"]==0


def test_missing_ticker_on_market_eligible_action_still_blocks():
    actions,etf=_frames()
    actions.loc[0,"yahoo_ticker"]=pd.NA
    result=_run(actions,etf)
    checks={c["check"]:c for c in result.checks}
    assert not result.passed
    assert not checks["actions_ticker_coverage_eligible_pct"]["passed"]
    assert not checks["actions_missing_ticker_explicitly_identity_only_pct"]["passed"]


def test_identity_only_row_with_ticker_requires_explicit_hydration_transition():
    actions,etf=_frames()
    actions.loc[2,"yahoo_ticker"]="A3.PA"
    result=_run(actions,etf)
    checks={c["check"]:c for c in result.checks}
    assert not result.passed
    assert not checks["actions_identity_only_rows_have_no_invented_ticker"]["passed"]
