from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from v182.sources import tradingview_technical as tv


def _html(symbol: str = "EURONEXT:AIR") -> str:
    return f"""
    <html><body>
      <a href="/chart/?symbol={symbol}">chart</a>
      <div>
        Our technical rating for Airbus SE is neutral today.
        Note that market conditions change all the time — according to our
        1 week rating the buy trend is prevailing, and 1 month rating shows
        the strong buy signal.
      </div>
    </body></html>
    """


class _Response:
    def __init__(self, html: str, url: str):
        self.text = html
        self.url = url

    def raise_for_status(self):
        return None


def test_parse_public_faq_returns_complete_1d_1w_1m_enum():
    fields = tv.parse_technical_summary_html(_html())
    assert fields["tradingview_daily_signal"] == "NEUTRAL"
    assert fields["tradingview_weekly_signal"] == "BUY"
    assert fields["tradingview_monthly_signal"] == "STRONG_BUY"
    assert fields["tradingview_technical_complete"] is True


def test_parse_is_fail_closed_when_one_timeframe_is_missing():
    html = _html().replace("1 month rating shows the strong buy signal", "monthly data unavailable")
    assert tv.parse_technical_summary_html(html) == {}


def test_symbol_is_deterministic_from_exchange_qualified_yahoo_ticker():
    assert tv.tradingview_symbol({"yahoo_ticker": "AIR.PA"}) == ("EURONEXT", "AIR")
    assert tv.technical_url({"yahoo_ticker": "STLAM.MI"}) == (
        "https://www.tradingview.com/symbols/MIL-STLAM/technicals/",
        "MIL:STLAM",
    )
    assert tv.tradingview_symbol({"yahoo_ticker": "AIR"}) is None


def test_collector_requires_exact_exchange_symbol_proof(tmp_path):
    rows = pd.DataFrame([
        {"isin": "NL0000235190", "asset_class": "ACTION", "horizon": "CT", "yahoo_ticker": "AIR.PA"}
    ])

    def wrong_symbol_fetcher(url: str, *, timeout: float):
        return _Response(_html("EURONEXT:CAP"), url)

    result = tv.collect_technical_context_cached(
        rows,
        tmp_path / "tv.json",
        fetcher=wrong_symbol_fetcher,
        request_start_interval_seconds=0,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert result.observations == []
    assert result.metrics["live_refresh_success"] == 0
    assert result.failures[0]["reason"] == "SYMBOL_IDENTITY_NOT_PROVEN"


def test_collector_emits_audited_three_timeframe_context(tmp_path):
    rows = pd.DataFrame([
        {"isin": "NL0000235190", "asset_class": "ACTION", "horizon": "CT", "yahoo_ticker": "AIR.PA"}
    ])

    def fetcher(url: str, *, timeout: float):
        return _Response(_html(), url)

    result = tv.collect_technical_context_cached(
        rows,
        tmp_path / "tv.json",
        fetcher=fetcher,
        request_start_interval_seconds=0,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    fields = {item["field"]: item["value"] for item in result.observations}
    assert result.metrics["live_refresh_success"] == 1
    assert fields["tradingview_daily_signal"] == "NEUTRAL"
    assert fields["tradingview_weekly_signal"] == "BUY"
    assert fields["tradingview_monthly_signal"] == "STRONG_BUY"
    assert fields["tradingview_symbol"] == "EURONEXT:AIR"
    assert fields["tradingview_horizon_signal"] == "BUY"

