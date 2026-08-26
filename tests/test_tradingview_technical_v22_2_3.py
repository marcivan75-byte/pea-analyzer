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


def test_parse_public_etf_faq_returns_complete_1d_1w_1m_enum():
    html = """
    <html><body>
      Our summary technical rating for EDEU is buy today.
      Note that market conditions change constantly — according to our
      1-week rating, the buy trend prevails, and 1 month rating shows
      the strong buy signal.
    </body></html>
    """
    fields = tv.parse_technical_summary_html(html)
    assert fields["tradingview_daily_signal"] == "BUY"
    assert fields["tradingview_weekly_signal"] == "BUY"
    assert fields["tradingview_monthly_signal"] == "STRONG_BUY"


def test_parse_is_fail_closed_when_one_timeframe_is_missing():
    html = _html().replace("1 month rating", "monthly rating")
    assert tv.parse_technical_summary_html(html) == {}


def test_symbol_is_deterministic_from_exchange_qualified_yahoo_ticker():
    assert tv.tradingview_symbol({"yahoo_ticker": "AIR.PA"}) == ("EURONEXT", "AIR")
    assert tv.tradingview_symbol({"yahoo_ticker": "HM-B.ST"}) == ("OMXSTO", "HM_B")
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
    assert len(fields["tradingview_page_sha256"]) == 64


def test_cache_is_bound_to_current_exchange_qualified_symbol(tmp_path):
    cache = tmp_path / "tv.json"
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)

    def fetcher(url: str, *, timeout: float):
        symbol = "EURONEXT:CAP" if "CAP" in url else "EURONEXT:AIR"
        return _Response(_html(symbol), url)

    air = pd.DataFrame([
        {"isin": "FR0000000001", "asset_class": "ACTION", "horizon": "CT", "yahoo_ticker": "AIR.PA"}
    ])
    cap = air.assign(yahoo_ticker="CAP.PA")
    first = tv.collect_technical_context_cached(
        air, cache, fetcher=fetcher, request_start_interval_seconds=0, now=now
    )
    second = tv.collect_technical_context_cached(
        cap, cache, fetcher=fetcher, request_start_interval_seconds=0, now=now
    )
    fields = {item["field"]: item["value"] for item in second.observations}
    assert first.metrics["live_refresh_success"] == 1
    assert second.metrics["identity_mismatch_rejected"] == 1
    assert second.metrics["live_refresh_success"] == 1
    assert fields["tradingview_symbol"] == "EURONEXT:CAP"


def test_stale_cache_is_not_reused_when_refresh_fails(tmp_path):
    rows = pd.DataFrame([
        {"isin": "FR0000000001", "asset_class": "ACTION", "horizon": "CT", "yahoo_ticker": "AIR.PA"}
    ])
    cache = tmp_path / "tv.json"
    first_now = datetime(2026, 8, 24, tzinfo=timezone.utc)

    def ok(url: str, *, timeout: float):
        return _Response(_html(), url)

    def unavailable(url: str, *, timeout: float):
        raise TimeoutError("source unavailable")

    tv.collect_technical_context_cached(
        rows, cache, fetcher=ok, request_start_interval_seconds=0, now=first_now
    )
    result = tv.collect_technical_context_cached(
        rows,
        cache,
        fetcher=unavailable,
        request_start_interval_seconds=0,
        now=datetime(2026, 8, 24, 7, tzinfo=timezone.utc),
    )
    assert result.observations == []
    assert result.metrics["stale_rejected"] == 1
    assert result.metrics["usable_rows"] == 0


def test_redirect_to_unexpected_host_is_rejected(tmp_path):
    rows = pd.DataFrame([
        {"isin": "FR0000000001", "asset_class": "ACTION", "horizon": "CT", "yahoo_ticker": "AIR.PA"}
    ])

    def redirected(url: str, *, timeout: float):
        return _Response(_html(), "https://example.invalid/symbols/EURONEXT-AIR/technicals/")

    result = tv.collect_technical_context_cached(
        rows,
        tmp_path / "tv.json",
        fetcher=redirected,
        request_start_interval_seconds=0,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert result.observations == []
    assert result.failures[0]["reason"] == "UNEXPECTED_FINAL_URL"
