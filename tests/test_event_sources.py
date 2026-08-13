from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from v182.reporting import event_sources


def test_finnhub_calendar_maps_unique_base_symbol_and_keeps_eps_revision_pit(monkeypatch,tmp_path:Path):
    actions=pd.DataFrame([{"isin":"FR0000120073","yahoo_ticker":"AIR.PA","name":"Airbus"}])

    monkeypatch.setattr(event_sources,"fetch_earnings_calendar",lambda *args,**kwargs: ([{
        "symbol":"AIR","date":"2026-08-17","hour":"bmo","eps_estimate":2.1,
        "revenue_estimate":1234.0,"quarter":2,"year":2026,
    }],[]))
    monkeypatch.setattr(event_sources,"fetch_eps_estimates",lambda *args,**kwargs: ([{
        "ticker":"AIR.PA","period":"2026-09-30","eps_avg":2.4,"eps_high":2.6,"eps_low":2.2,
        "number_analysts":12,"quarter":3,"year":2026,
    }],[],{"status":"SUCCESS_WITH_POSSIBLE_GAPS"}))
    monkeypatch.setattr(event_sources,"update_eps_history",lambda rows,mapping,path,as_of=None: ([{
        "isin":"FR0000120073","ticker":"AIR.PA","fields":{"eps_estimate_finnhub":2.4,"eps_revision_30d":5.0},
    }],{"snapshots_added":1,"derived_rows":1,"state_path":str(path)}))

    observations,failures,stats=event_sources.collect_finnhub_earnings(
        actions,"key",tmp_path/"eps.csv",as_of=date(2026,8,13),calendar_days=35,
    )
    assert failures == []
    by_field={o["field"]:o for o in observations}
    assert by_field["days_to_earnings"]["value"] == 4
    assert by_field["days_to_earnings"]["source"] == "Finnhub Earnings Calendar"
    assert by_field["days_to_earnings"]["evidence_level"] == "B"
    assert by_field["eps_revision_30d"]["value"] == 5.0
    assert by_field["eps_revision_30d"]["source"] == "Finnhub EPS Estimates"
    assert stats["source_status"] == "SUCCESS"
    assert stats["no_revision_imputation"] is True


def test_amf_source_filters_to_canonical_actions_and_never_creates_zero(monkeypatch):
    actions=pd.DataFrame([{"isin":"FR0000120271","yahoo_ticker":"TTE.PA"},{"isin":"FR0000120073","yahoo_ticker":"AIR.PA"}])
    monkeypatch.setattr(event_sources,"fetch_current_public_shorts",lambda as_of=None: ([
        {"isin":"FR0000120271","public_short_pct":0.8,"amf_public_net_short_pct":0.8,"amf_public_short_below_0_5_warning":False},
        {"isin":"FR9999999999","public_short_pct":1.2,"amf_public_net_short_pct":1.2,"amf_public_short_below_0_5_warning":False},
    ],[],{"status":"SUCCESS","active_isins":2,"no_zero_imputation":True}))
    observations,failures,stats=event_sources.collect_amf_short_positions(actions,as_of=date(2026,8,13))
    assert failures == []
    assert {o["isin"] for o in observations} == {"FR0000120271"}
    assert all(o["source"] == "AMF Open Data - Positions courtes nettes" for o in observations)
    assert all(o["evidence_level"] == "A" for o in observations)
    assert not any(o["isin"] == "FR0000120073" and o["field"] == "public_short_pct" for o in observations)
    assert stats["absence_means_zero"] is False
    assert stats["source_status"] == "SUCCESS"
