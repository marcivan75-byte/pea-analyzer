from __future__ import annotations

import sys
from types import ModuleType

from v182.sources.finnhub_consensus import fetch_consensus
from v182.sources.yfinance_info import collect_info


def test_yfinance_info_bounded_workers_preserve_observations(monkeypatch):
    fake_yf=ModuleType("yfinance")

    class FakeTicker:
        def __init__(self,ticker):
            self.ticker=ticker

        def get_info(self):
            return {"marketCap":100 if self.ticker=="AAA" else 200,"quoteType":"EQUITY"}

    fake_yf.Ticker=FakeTicker
    monkeypatch.setitem(sys.modules,"yfinance",fake_yf)
    observations,failures=collect_info(["BBB","AAA","AAA"],delay_seconds=0,max_workers=2)
    assert failures==[]
    assert {(r["ticker"],r["field"],r["value"]) for r in observations}=={
        ("AAA","market_cap",100),("AAA","quote_type_yf","EQUITY"),
        ("BBB","market_cap",200),("BBB","quote_type_yf","EQUITY"),
    }


def test_finnhub_bounded_workers_preserve_consensus_and_target(monkeypatch):
    fake_requests=ModuleType("requests")

    class FakeResponse:
        def __init__(self,payload,ok=True):
            self._payload=payload
            self.ok=ok

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError("HTTP_ERROR")

        def json(self):
            return self._payload

    def fake_get(url,params,timeout):
        assert timeout==15
        ticker=params["symbol"]
        if url.endswith("/stock/recommendation"):
            return FakeResponse([
                {"period":"2026-08-01","strongBuy":2,"buy":3,"hold":1,"sell":0,"strongSell":0},
                {"period":"2026-07-01","strongBuy":1,"buy":2,"hold":2,"sell":1,"strongSell":0},
            ])
        return FakeResponse({"targetMean":123.0 if ticker=="AAA" else 234.0,"lastUpdated":"2026-08-12"})

    fake_requests.get=fake_get
    monkeypatch.setitem(sys.modules,"requests",fake_requests)
    observations,failures=fetch_consensus(["BBB","AAA","AAA"],"secret",delay_seconds=0,max_workers=4)
    assert failures==[]
    targets={(r["ticker"],r["value"]) for r in observations if r["field"]=="target_price"}
    assert targets=={("AAA",123.0),("BBB",234.0)}
    assert {(r["ticker"],r["field"]) for r in observations if r["field"]=="consensus_delta_4w"}=={
        ("AAA","consensus_delta_4w"),("BBB","consensus_delta_4w")
    }
