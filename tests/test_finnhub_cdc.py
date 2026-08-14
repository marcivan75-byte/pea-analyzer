from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd

from v182.sources.finnhub_cdc import build_calendar_observations


def _value(observations: list[dict], field: str):
    return next(item["value"] for item in observations if item["field"] == field)


def test_first_eps_snapshot_has_no_revision_then_same_period_revision_is_pit(tmp_path):
    actions=pd.DataFrame([{"isin":"FR0000120073","yahoo_ticker":"AIR.PA"}])
    history=tmp_path/"eps.csv"
    first=[{"symbol":"AIR","date":"2026-09-10","year":2026,"quarter":3,"epsEstimate":2.0,"revenueEstimate":100.0,"hour":"amc"}]
    obs1,fail1=build_calendar_observations(actions,first,history,observed_at=datetime(2026,8,14,tzinfo=timezone.utc))
    assert not fail1
    assert _value(obs1,"eps_estimate_next_fh")==2.0
    assert not any(x["field"]=="eps_estimate_revision_pct_fh" for x in obs1)

    second=[{"symbol":"AIR","date":"2026-09-10","year":2026,"quarter":3,"epsEstimate":2.2,"revenueEstimate":101.0,"hour":"amc"}]
    obs2,fail2=build_calendar_observations(actions,second,history,observed_at=datetime(2026,8,15,tzinfo=timezone.utc))
    assert not fail2
    assert round(float(_value(obs2,"eps_estimate_revision_abs_fh")),6)==0.2
    assert round(float(_value(obs2,"eps_estimate_revision_pct_fh")),6)==10.0


def test_unmatched_finnhub_symbol_does_not_create_observation(tmp_path):
    actions=pd.DataFrame([{"isin":"FR0000120073","yahoo_ticker":"AIR.PA"}])
    rows=[{"symbol":"UNKNOWN","date":"2026-09-10","year":2026,"quarter":3,"epsEstimate":1.0}]
    observations,failures=build_calendar_observations(actions,rows,tmp_path/"eps.csv",observed_at=datetime(2026,8,14,tzinfo=timezone.utc))
    assert observations==[]
    assert failures==[]


def test_ambiguous_bare_symbol_is_quarantined_but_exact_exchange_symbol_is_safe(tmp_path):
    actions=pd.DataFrame([
        {"isin":"FR0000000001","yahoo_ticker":"ABC.PA"},
        {"isin":"NL0000000002","yahoo_ticker":"ABC.AS"},
    ])
    bare=[{"symbol":"ABC","date":"2026-09-10","year":2026,"quarter":3,"epsEstimate":1.0}]
    observations,failures=build_calendar_observations(actions,bare,tmp_path/"eps.csv",observed_at=datetime(2026,8,14,tzinfo=timezone.utc))
    assert observations==[]
    assert failures and failures[0]["reason"]=="AMBIGUOUS_SYMBOL_ALIAS:ABC"

    exact=[{"symbol":"ABC.PA","date":"2026-09-10","year":2026,"quarter":3,"epsEstimate":1.1}]
    observations2,failures2=build_calendar_observations(actions,exact,tmp_path/"eps.csv",observed_at=datetime(2026,8,14,tzinfo=timezone.utc))
    assert not failures2
    assert {row["isin"] for row in observations2}=={"FR0000000001"}
