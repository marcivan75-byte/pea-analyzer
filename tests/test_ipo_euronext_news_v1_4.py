from __future__ import annotations

from types import SimpleNamespace

from v182.decision import ipo_radar_v1_4 as v14
from v182.sources import euronext_ipo_news_v1_4 as news


POLAR_HTML = """
<html><body>
<h1>Polar Resources - Private placement and retail offering successfully completed</h1>
<p>The Company raised a total of NOK 50 million in gross proceeds.</p>
<p>The offering comprised 7,142,857 new shares at a subscription price of NOK 7.00 per share.</p>
<p>The Company has 14,252,857 issued shares after registration.</p>
<p>The offering attracted significant interest from Nordic and international investors.</p>
<p>Trafigura will join as a cornerstone investor for an aggregate amount of NOK 12,000,000.</p>
<p>The following members of the Board and management have pre-committed to subscribe.</p>
<p>A separate Retail Offering was completed.</p>
<p>The Company has prepared a national Prospectus.</p>
<a href="/documents/polar-prospectus.pdf">Prospectus</a>
</body></html>
"""


def test_parse_euronext_regulated_news_extracts_shadow_facts() -> None:
    result = news.parse_regulated_news(POLAR_HTML, "https://live.euronext.com/en/products/equities/company-news/example")
    assert result["euronext_news_parse_status"] == "SUCCESS"
    assert result["euronext_news_gross_proceeds_local"] == 50_000_000.0
    assert result["euronext_news_gross_proceeds_currency"] == "NOK"
    assert result["euronext_news_offer_price_local"] == 7.0
    assert result["euronext_news_offer_price_currency"] == "NOK"
    assert result["euronext_news_cornerstone_amount_local"] == 12_000_000.0
    assert result["euronext_news_new_shares"] == 7_142_857
    assert result["euronext_news_issued_shares"] == 14_252_857
    assert result["euronext_news_demand_signal_shadow"] == "STRONG_DEMAND"
    assert result["euronext_news_primary_offer_detected"] is True
    assert result["euronext_news_retail_offer_detected"] is True
    assert result["euronext_news_management_commitment_detected"] is True
    assert result["euronext_news_cornerstone_detected"] is True
    assert result["euronext_news_prospectus_reference_detected"] is True
    assert "polar-prospectus.pdf" in result["euronext_news_document_urls"]


def test_money_parser_does_not_bind_offer_price_to_gross_proceeds() -> None:
    html = "<p>The offering comprised shares at NOK 7.00 per share and raised NOK 50 million in gross proceeds.</p>"
    result = news.parse_regulated_news(html)
    assert result["euronext_news_gross_proceeds_local"] == 50_000_000.0
    assert result["euronext_news_offer_price_local"] is None


def test_official_isin_listview_keeps_only_ipo_relevant_company_news(monkeypatch) -> None:
    listview_html = """
    <html><body>
      <a href="/en/products/equities/company-news/2026-07-02-polar-private-placement-successfully-completed">Private placement successfully completed</a>
      <a href="/en/products/equities/company-news/2026-07-03-polar-mandatory-notification-trade">Mandatory notification of trade</a>
      <a href="/en/products/equities/company-news/2026-07-09-polar-first-day-trading-information-document">First day of trading and information document</a>
    </body></html>
    """
    monkeypatch.setattr(
        news.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(text=listview_html, raise_for_status=lambda: None),
    )
    urls, error, source = news._official_listview_urls("NO0013756361")
    assert error is None
    assert source.endswith("/NO0013756361")
    assert len(urls) == 2
    assert any("private-placement" in url for url in urls)
    assert any("first-day-trading" in url for url in urls)
    assert all("mandatory-notification" not in url for url in urls)


def test_gdelt_fallback_keeps_only_official_euronext_company_news(monkeypatch) -> None:
    articles = [
        {"url": "https://live.euronext.com/en/products/equities/company-news/2026-07-02-polar-offering"},
        {"url": "https://example.com/polar-ipo"},
        {"url": "https://live.euronext.com/en/ipo-showcase/polar-resources"},
    ]
    monkeypatch.setattr(news.gdelt_news, "fetch_articles", lambda *args, **kwargs: (articles, None))
    urls, error = news._article_urls("Polar Resources AS")
    assert error is None
    assert urls == ["https://live.euronext.com/en/products/equities/company-news/2026-07-02-polar-offering"]


def test_direct_discovery_is_primary_and_shadow_only(monkeypatch) -> None:
    url = "https://live.euronext.com/en/products/equities/company-news/2026-07-02-polar-offering"
    monkeypatch.setattr(news, "_official_listview_urls", lambda *args, **kwargs: ([url], None, "https://live.euronext.com/listview"))
    monkeypatch.setattr(news, "_article_urls", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback must not run")))
    monkeypatch.setattr(
        news.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(text=POLAR_HTML, raise_for_status=lambda: None),
    )
    candidate = news.enrich_candidate({"name": "Polar Resources AS", "isin": "NO0013756361", "source": "EURONEXT"})
    assert candidate["euronext_news_discovery_status"] == "DIRECT_SUCCESS"
    assert candidate["euronext_news_discovery_method"] == "EURONEXT_ISIN_LISTVIEW"
    assert candidate["euronext_news_fetch_success_count"] == 1
    assert candidate["euronext_news_demand_signal_shadow"] == "STRONG_DEMAND"
    assert candidate["euronext_news_evidence_policy"] == "SHADOW_FACTS_ONLY_NO_ACTIVE_SCORE_V1.4"
    assert "opportunity_bookbuilding_demand" not in candidate
    assert "risk_dilution_secondary" not in candidate
    assert "live_order_allowed" not in candidate


def test_gdelt_is_only_fallback_when_direct_discovery_has_no_match(monkeypatch) -> None:
    url = "https://live.euronext.com/en/products/equities/company-news/2026-07-02-polar-offering"
    monkeypatch.setattr(news, "_official_listview_urls", lambda *args, **kwargs: ([], None, "https://live.euronext.com/listview"))
    monkeypatch.setattr(news, "_article_urls", lambda *args, **kwargs: ([url], None))
    monkeypatch.setattr(
        news.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(text=POLAR_HTML, raise_for_status=lambda: None),
    )
    candidate = news.enrich_candidate({"name": "Polar Resources AS", "isin": "NO0013756361", "source": "EURONEXT"})
    assert candidate["euronext_news_discovery_status"] == "GDELT_FALLBACK_SUCCESS"
    assert candidate["euronext_news_discovery_method"] == "GDELT_FALLBACK"


def test_non_euronext_candidate_is_not_enriched(monkeypatch) -> None:
    monkeypatch.setattr(news, "_official_listview_urls", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not query")))
    candidate = {"name": "US IPO Inc", "source": "FINNHUB"}
    assert news.enrich_candidate(candidate) == candidate


def test_v1_4_collector_survives_v1_3_runtime_reinstall() -> None:
    original = v14._BASE_EURONEXT
    try:
        v14.v13.euronext_ipo_v1_3.collect_euronext_v1_3 = original
        v14.v13.install_v1_3()
        v14.install_v1_4()
        v14.v13.install_v1_3()
        assert v14.legacy.collect_euronext is v14.collect_euronext_v1_4
    finally:
        v14.v13.euronext_ipo_v1_3.collect_euronext_v1_3 = original
        v14.v13.install_v1_3()
