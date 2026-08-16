from __future__ import annotations

from v182.decision.ipo_radar_v1_2 import market_readiness_v1_2
from v182.sources import sec_ipo_deep_v1_2 as deep


def _synthetic_ixbrl() -> str:
    return """
    <html><body>
      <xbrli:context id="FY2024"><xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="FY2025"><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="I2025"><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
      <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="FY2024" scale="6">100</ix:nonFraction>
      <ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="FY2025" scale="6">140</ix:nonFraction>
      <ix:nonFraction name="us-gaap:GrossProfit" contextRef="FY2025" scale="6">70</ix:nonFraction>
      <ix:nonFraction name="us-gaap:OperatingIncomeLoss" contextRef="FY2024" scale="6" sign="-">10</ix:nonFraction>
      <ix:nonFraction name="us-gaap:OperatingIncomeLoss" contextRef="FY2025" scale="6">0</ix:nonFraction>
      <ix:nonFraction name="us-gaap:NetIncomeLoss" contextRef="FY2025" scale="6" sign="-">15</ix:nonFraction>
      <ix:nonFraction name="us-gaap:NetCashProvidedByUsedInOperatingActivities" contextRef="FY2025" scale="6" sign="-">20</ix:nonFraction>
      <ix:nonFraction name="us-gaap:CashAndCashEquivalentsAtCarryingValue" contextRef="I2025" scale="6">30</ix:nonFraction>
      <ix:nonFraction name="us-gaap:Assets" contextRef="I2025" scale="6">120</ix:nonFraction>
      <ix:nonFraction name="us-gaap:Liabilities" contextRef="I2025" scale="6">60</ix:nonFraction>
    </body></html>
    """


def test_inline_xbrl_preferred_financials_are_period_aligned() -> None:
    scores = deep.financial_scores_from_inline_xbrl(_synthetic_ixbrl())
    assert scores["sec_ixbrl_status"] == "SUCCESS"
    assert scores["sec_latest_revenue"] == 140_000_000
    assert scores["sec_revenue_growth_pct"] == 40.0
    assert scores["opportunity_revenue_growth"] == 92.0
    assert scores["sec_latest_gross_margin_pct"] == 50.0
    assert scores["opportunity_gross_margin_quality"] == 80.0
    assert scores["opportunity_operating_leverage"] == 75.0
    assert scores["sec_cash_runway_years_pre_ipo"] == 1.5
    assert scores["opportunity_balance_sheet_post_ipo"] is None


def test_offer_terms_reconstruct_dilution_and_pro_forma_metrics_without_fake_peer_score() -> None:
    text = """
    We are offering 10,000,000 shares of common stock at an initial public offering price of $10.00 per share.
    Selling stockholders are offering 2,000,000 shares of common stock.
    50,000,000 shares of common stock will be outstanding immediately after this offering.
    We estimate that the net proceeds to us from this offering will be approximately $90 million.
    New investors will experience immediate dilution of $6.00 per share.
    """
    financial = deep.financial_scores_from_inline_xbrl(_synthetic_ixbrl())
    terms = deep.extract_offer_terms(text)
    metrics = deep._post_ipo_metrics(financial, terms)
    assert terms["sec_ipo_price"] == 10.0
    assert terms["sec_primary_shares_offered"] == 10_000_000
    assert terms["sec_secondary_shares_offered"] == 2_000_000
    assert terms["sec_post_offering_shares"] == 50_000_000
    assert terms["sec_net_proceeds"] == 90_000_000
    assert terms["sec_dilution_pct"] == 60.0
    assert terms["sec_implied_market_cap"] == 500_000_000
    assert deep._dilution_risk(terms) == 78.0
    assert metrics["sec_cash_runway_years_post_ipo_upper_bound"] == 6.0
    assert metrics["opportunity_balance_sheet_post_ipo"] is not None
    assert metrics["sec_ipo_price_to_sales"] == 3.57
    assert metrics["shadow_absolute_valuation_risk"] == 35.0
    assert "risk_valuation" not in metrics
    assert metrics["hard_flags"] == ""


def test_liquidity_hard_block_only_when_even_upper_bound_runway_is_under_one_year() -> None:
    financial = {
        "sec_cash": 5_000_000.0,
        "sec_assets": 50_000_000.0,
        "sec_liabilities": 30_000_000.0,
        "sec_latest_operating_cash_flow": -20_000_000.0,
        "sec_latest_revenue": 40_000_000.0,
    }
    terms = {"sec_net_proceeds": 10_000_000.0, "sec_implied_market_cap": 200_000_000.0}
    metrics = deep._post_ipo_metrics(financial, terms)
    assert metrics["sec_cash_runway_years_post_ipo_upper_bound"] == 0.75
    assert metrics["hard_flags"] == "insufficient_12m_liquidity_post_offering"


def test_v1_2_deep_prospectus_keeps_market_readiness_credit() -> None:
    row = {
        "status": "expected",
        "exchange": "NASDAQ",
        "offer_value": 200_000_000,
        "price_low": 10,
        "price_high": 11,
        "price_mid": 10.5,
        "source_count": 2,
        "sec_analysis_status": "PROSPECTUS_DEEP_PARSED_V1_2",
    }
    assert market_readiness_v1_2(row) >= 70
