from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from v182.sources.finnhub_earnings import (
    fetch_earnings_calendar,
    fetch_eps_estimates,
    update_eps_history,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload=payload
        self.status_code=status_code
        self.ok=200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP_{self.status_code}")


class CalendarRequests:
    calls=[]

    @classmethod
    def get(cls, url, params=None, timeout=None):
        cls.calls.append((url,params,timeout))
        return FakeResponse({"earningsCalendar":[{
            "date":"2026-08-17","epsActual":None,"epsEstimate":2.1,"hour":"bmo",
            "quarter":2,"revenueActual":None,"revenueEstimate":1234.0,"symbol":"AIR","year":2026,
        }]})


class PremiumDeniedRequests:
    calls=[]

    @classmethod
    def get(cls, url, params=None, timeout=None):
        cls.calls.append((url,params,timeout))
        return FakeResponse({"error":"Premium access required"},status_code=403)


def test_earnings_calendar_parses_international_event():
    CalendarRequests.calls=[]
    rows,failures=fetch_earnings_calendar(
        "key",from_date=date(2026,8,13),to_date=date(2026,8,20),
        requests_module=CalendarRequests,
    )
    assert failures == []
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AIR"
    assert rows[0]["date"] == "2026-08-17"
    assert rows[0]["eps_estimate"] == 2.1
    assert CalendarRequests.calls[0][1]["international"] == "true"


def test_eps_premium_denial_stops_after_one_capability_probe():
    PremiumDeniedRequests.calls=[]
    rows,failures,status=fetch_eps_estimates(
        ["AAA","BBB","CCC"],"key",delay_seconds=0.0,requests_module=PremiumDeniedRequests,
        as_of=date(2026,8,13),
    )
    assert rows == []
    assert len(PremiumDeniedRequests.calls) == 1
    assert failures[0]["reason"] == "PREMIUM_ACCESS_REQUIRED_OR_UNAUTHORIZED"
    assert status["status"] == "PREMIUM_ACCESS_REQUIRED_OR_UNAUTHORIZED"
    assert status["skipped_after_probe"] == 2


def test_eps_history_derives_only_same_period_pit_revisions(tmp_path:Path):
    path=tmp_path/"EPS_ESTIMATE_HISTORY.csv"
    history=pd.DataFrame([
        {"snapshot_date":"2026-05-01","isin":"FR0000120073","ticker":"AIR.PA","period":"2026-09-30","eps_avg":"2.00","eps_high":"2.2","eps_low":"1.8","number_analysts":"10","quarter":"3","year":"2026"},
        {"snapshot_date":"2026-07-01","isin":"FR0000120073","ticker":"AIR.PA","period":"2026-09-30","eps_avg":"2.20","eps_high":"2.4","eps_low":"2.0","number_analysts":"11","quarter":"3","year":"2026"},
        {"snapshot_date":"2026-05-01","isin":"FR0000120073","ticker":"AIR.PA","period":"2026-06-30","eps_avg":"9.99","eps_high":"10","eps_low":"9","number_analysts":"9","quarter":"2","year":"2026"},
    ])
    history.to_csv(path,sep=";",index=False,encoding="utf-8-sig")
    current=[{"ticker":"AIR.PA","period":"2026-09-30","eps_avg":2.42,"eps_high":2.6,"eps_low":2.2,"number_analysts":12,"quarter":3,"year":2026}]
    derived,status=update_eps_history(current,{"AIR.PA":"FR0000120073"},path,as_of=date(2026,8,13))
    fields=derived[0]["fields"]
    assert round(fields["eps_revision_30d"],6) == 10.0
    assert round(fields["eps_revision_3m"],6) == 21.0
    assert fields["eps_estimate_finnhub"] == 2.42
    assert status["snapshots_added"] == 1


def test_cross_period_snapshot_is_not_called_revision(tmp_path:Path):
    path=tmp_path/"EPS_ESTIMATE_HISTORY.csv"
    pd.DataFrame([{
        "snapshot_date":"2026-05-01","isin":"FR0000120073","ticker":"AIR.PA","period":"2026-06-30",
        "eps_avg":"2.00","eps_high":"2.2","eps_low":"1.8","number_analysts":"10","quarter":"2","year":"2026",
    }]).to_csv(path,sep=";",index=False,encoding="utf-8-sig")
    current=[{"ticker":"AIR.PA","period":"2026-09-30","eps_avg":2.42,"eps_high":2.6,"eps_low":2.2,"number_analysts":12,"quarter":3,"year":2026}]
    derived,_=update_eps_history(current,{"AIR.PA":"FR0000120073"},path,as_of=date(2026,8,13))
    fields=derived[0]["fields"]
    assert "eps_revision_30d" not in fields
    assert "eps_revision_3m" not in fields
