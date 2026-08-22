from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from v182.sources.investing_technical import (
    _candidate_base_urls,
    collect_technical_context_cached,
    horizon_signal,
    parse_technical_summary_html,
)


def test_parse_english_daily_weekly_monthly_summary():
    html = "<html><body>Technical summary 30 Min Neutral Hourly Sell 5 Hours Sell Daily Strong Sell Weekly Neutral Monthly Strong Buy</body></html>"
    fields = parse_technical_summary_html(html)
    assert fields["investing_daily_signal"] == "STRONG_SELL"
    assert fields["investing_daily_score"] == -2
    assert fields["investing_weekly_signal"] == "NEUTRAL"
    assert fields["investing_monthly_signal"] == "STRONG_BUY"
    assert fields["investing_technical_complete"] is True


def test_parse_french_summary_and_horizon_mapping():
    html = "<html><body>Récapitulatif technique 30 min Achat Fort 1 heure Achat 5 heures Vente Journalier Vente Forte Hebdomadaire Neutre Mensuel Achat Fort</body></html>"
    fields = parse_technical_summary_html(html)
    assert horizon_signal(fields, "TCT") == ("STRONG_SELL", -2)
    assert horizon_signal(fields, "CT") == ("NEUTRAL", 0)
    assert horizon_signal(fields, "MT") == ("STRONG_BUY", 2)


def test_company_slug_preserves_semantic_holding_word():
    urls = _candidate_base_urls(
        {
            "asset_class": "ACTION",
            "name": "ASML Holding NV",
            "yahoo_ticker": "ASML.AS",
        }
    )
    assert "https://www.investing.com/equities/asml-holding" in urls


class FakeResponse:
    def __init__(self, text: str, url: str):
        self.text = text
        self.url = url

    def raise_for_status(self):
        return None


def test_selected_cache_resolves_by_isin_then_reuses_without_raw_html(tmp_path: Path):
    calls = []

    def fetcher(url, timeout):
        calls.append(url)
        if url.endswith("-technical"):
            return FakeResponse(
                "<html><body>Daily Sell Weekly Buy Monthly Strong Buy</body></html>",
                url,
            )
        return FakeResponse("<html><body>ISIN FR0000120073 Air Liquide SA</body></html>", url)

    rows = pd.DataFrame(
        [
            {
                "isin": "FR0000120073",
                "asset_class": "ACTION",
                "horizon": "CT",
                "name": "Air Liquide SA",
                "yahoo_ticker": "AI.PA",
            }
        ]
    )
    cache = tmp_path / "tech.json"
    mapping = tmp_path / "urls.json"
    now = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)
    first = collect_technical_context_cached(
        rows,
        cache,
        mapping,
        request_start_interval_seconds=0,
        fetcher=fetcher,
        now=now,
    )
    first_call_count = len(calls)
    second = collect_technical_context_cached(
        rows,
        cache,
        mapping,
        request_start_interval_seconds=0,
        fetcher=fetcher,
        now=now,
    )
    assert first.metrics["live_refresh_success"] == 1
    assert second.metrics["live_refresh_requested"] == 0
    assert len(calls) == first_call_count
    assert any(row["field"] == "investing_horizon_signal" and row["value"] == "BUY" for row in second.observations)
    assert "<html>" not in cache.read_text(encoding="utf-8")
    assert "<html>" not in mapping.read_text(encoding="utf-8")
