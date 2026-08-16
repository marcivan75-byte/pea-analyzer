from __future__ import annotations

import pandas as pd

from v182.decision.ipo_outcomes_v1 import _price_metrics, _validation_summary, yahoo_ticker


def test_yahoo_ticker_mapping() -> None:
    assert yahoo_ticker("ABCD", "NASDAQ") == "ABCD"
    assert yahoo_ticker("TEST", "Euronext", "Paris") == "TEST.PA"
    assert yahoo_ticker("TEST", "Euronext Growth", "Milan") == "TEST.MI"
    assert yahoo_ticker("", "NASDAQ") is None


def test_post_ipo_price_metrics() -> None:
    prices = pd.DataFrame({"Close": [10.0 + i * 0.1 for i in range(65)]})
    metrics = _price_metrics(prices, 9.0)
    assert metrics["first_close"] == 10.0
    assert metrics["ret_first_close_vs_offer_pct"] == 11.11
    assert metrics["ret_d5_from_first_close_pct"] == 4.0
    assert metrics["ret_d20_from_first_close_pct"] == 19.0
    assert metrics["ret_d60_from_first_close_pct"] == 59.0


def test_validation_summary_never_auto_promotes() -> None:
    outcomes = pd.DataFrame([
        {"decision_pre_listing": "PRIORITY_DD", "ret_d20_from_first_close_pct": 10.0},
        {"decision_pre_listing": "PRIORITY_DD", "ret_d20_from_first_close_pct": -5.0},
        {"decision_pre_listing": "WATCH", "ret_d20_from_first_close_pct": 2.0},
    ])
    summary = _validation_summary(outcomes, "2026-08-16T08:00:00Z")
    assert summary["d20_sample_count"] == 3
    assert summary["by_prelisting_decision"]["PRIORITY_DD"]["positive_rate_pct"] == 50.0
    assert summary["promotion_ready"] is False
