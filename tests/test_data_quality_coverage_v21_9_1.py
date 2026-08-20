from __future__ import annotations

import pandas as pd

from v182.audit.master_data_profile import profile_frame
from v182.reporting import waves
from v182.reporting.waves import _yahoo_expense_ratio_pct, _yahoo_total_assets_eur_m
from v182.sources import finnhub_consensus
from v182.sources.yfinance_info import FIELDS


def test_finnhub_detail_redacts_query_and_bearer_secrets():
    raw=(
        "403 for https://finnhub.io/api/v1/stock/recommendation?symbol=ABC&token=SECRET123 "
        "Authorization: Bearer OTHERSECRET"
    )
    cleaned=finnhub_consensus._sanitize_detail(raw,limit=500)
    assert "SECRET123" not in cleaned
    assert "OTHERSECRET" not in cleaned
    assert "token=<REDACTED>" in cleaned
    assert "Bearer <REDACTED>" in cleaned


class _ForbiddenResponse:
    status_code=403
    ok=False

    def raise_for_status(self):
        raise AssertionError("raise_for_status should not be called for explicit 403 handling")

    def json(self):
        return {}


class _FakeRequests:
    def __init__(self):
        self.calls=[]

    def get(self,url,params=None,timeout=None):
        self.calls.append((url,dict(params or {}),timeout))
        return _ForbiddenResponse()


def test_finnhub_auth_denial_preflight_stops_further_calls(monkeypatch):
    fake=_FakeRequests()
    monkeypatch.setitem(__import__("sys").modules,"requests",fake)
    observations,failures=finnhub_consensus.fetch_consensus(
        ["AAA.PA","BBB.PA","CCC.PA"],"TOP_SECRET",delay_seconds=0,max_workers=3,
    )
    assert observations == []
    assert len(fake.calls) == 1
    assert any(row["reason"] == "FINNHUB_SOURCE_DISABLED_AUTH_OR_ENTITLEMENT" for row in failures)
    rendered=str(failures)
    assert "TOP_SECRET" not in rendered


def test_yahoo_etf_expense_ratio_conversion_is_unit_safe():
    assert _yahoo_expense_ratio_pct(0.0075) == 0.75
    assert _yahoo_expense_ratio_pct("0.0018") == 0.18
    assert _yahoo_expense_ratio_pct(-0.01) is None
    assert _yahoo_expense_ratio_pct(1.2) is None
    assert _yahoo_expense_ratio_pct("not-a-number") is None


def test_yahoo_etf_assets_conversion_requires_dedicated_asset_currency():
    assert _yahoo_total_assets_eur_m(832_159_936,"EUR") == 832.159936
    assert _yahoo_total_assets_eur_m(832_159_936,"USD") is None
    assert _yahoo_total_assets_eur_m(832_159_936,None) is None
    assert _yahoo_total_assets_eur_m(-1,"EUR") is None


def test_yahoo_quote_currency_does_not_promote_total_assets_to_eur_aum(monkeypatch):
    frame=pd.DataFrame([{"isin":"FR0010000001","name":"ETF","yahoo_ticker":"ETF.PA"}])
    raw=[
        {"ticker":"ETF.PA","field":"total_assets_yf","value":832_159_936,"source":"yfinance"},
        {"ticker":"ETF.PA","field":"currency_yf","value":"EUR","source":"yfinance"},
    ]
    monkeypatch.setattr(waves,"collect_info",lambda *args,**kwargs:(raw,[]))
    observations,failures=waves.wave6_etf_info(frame,{"yfinance":{"info_delay_seconds":0}})
    assert failures == []
    fields={row["field"] for row in observations}
    assert "total_assets_yf" in fields
    assert "currency_yf" in fields
    assert "aum_m" not in fields
    assert "fund_total_assets_eur_m" not in fields


def test_yfinance_collects_venue_and_raw_etf_fields():
    assert FIELDS["exchange"] == "exchange_yf"
    assert FIELDS["currency"] == "currency_yf"
    assert FIELDS["annualReportExpenseRatio"] == "annual_report_expense_ratio_yf"
    assert FIELDS["totalAssets"] == "total_assets_yf"


def test_profile_uses_semantic_aliases_without_copying_legacy_columns():
    frame=pd.DataFrame([
        {
            "isin":"FR0000000001","name":"A","yahoo_ticker":"A.PA",
            "sector_yf":"Industrials","industry_yf":"Machinery","country_yf":"France",
            "exchange_yf":"PAR","currency_yf":"EUR","target_mean_yf":"100",
            "n_analysts_yf":"8",
        },
        {
            "isin":"FR0000000002","name":"B","yahoo_ticker":"B.PA",
            "sector_yf":"Technology","industry_yf":"Software","country_yf":"France",
            "exchange_yf":"PAR","currency_yf":"EUR","target_mean_yf":"80",
            "n_analysts_yf":"5",
        },
    ])
    details,summary=profile_frame(frame,"ACTION")
    sector=details[(details["group"]=="qualitative") & (details["field"]=="sector")].iloc[0]
    exchange=details[(details["group"]=="identity") & (details["field"]=="exchange")].iloc[0]
    target=details[(details["group"]=="quantitative_fundamental") & (details["field"]=="target_price")].iloc[0]
    assert sector["coverage_pct"] == 100.0
    assert sector["resolved_columns"] == "sector_yf"
    assert exchange["coverage_pct"] == 100.0
    assert target["coverage_pct"] == 100.0
    assert "sector" not in frame.columns
    assert summary["qualitative"]["semantic_aliases_enabled"] is True
