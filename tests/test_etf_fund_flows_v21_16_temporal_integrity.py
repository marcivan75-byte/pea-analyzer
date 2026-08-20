from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from v182.features.etf_fund_flows_v1 import build_flow_computation, compute_daily_flows
from v182.sources.etf_fund_flows import _yfinance_snapshot

ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ETF_FUND_FLOW_V1_SHADOW.json").read_text(encoding="utf-8"))


def _row(date: str, **overrides) -> dict:
    row = {
        "instrument_id": "A",
        "as_of": date,
        "name": "A",
        "universe": "EXTERNAL",
        "asset_class": "ETF",
        "economic_family": "WORLD",
        "region": "US",
        "sector_or_theme": "TECHNOLOGY",
        "source": "test",
        "source_type": "ISSUER_OFFICIAL",
        "confidence": "A",
        "source_priority": 100,
        "aum": None,
        "nav": None,
        "shares_outstanding": None,
        "market_price": None,
        "distribution_per_share": 0.0,
        "is_pea": False,
        "is_inverse_or_leveraged": False,
        "is_synthetic": False,
        "isin": "",
        "ticker": "",
        "provider": "",
        "benchmark": "",
        "currency": "USD",
        "aum_as_of_explicit": True,
        "nav_as_of_explicit": True,
        "shares_as_of_explicit": True,
        "market_price_as_of_explicit": True,
    }
    row.update(overrides)
    return row


def test_undated_shares_are_suppressed_instead_of_creating_false_zero_flows():
    history = pd.DataFrame([
        _row(
            "2026-08-18",
            nav=10.0,
            shares_outstanding=100.0,
            market_price=10.0,
            nav_as_of_explicit=False,
            shares_as_of_explicit=False,
        ),
        _row(
            "2026-08-19",
            nav=10.0,
            shares_outstanding=100.0,
            market_price=11.0,
            nav_as_of_explicit=False,
            shares_as_of_explicit=False,
        ),
    ])
    last = compute_daily_flows(history).iloc[-1]
    assert bool(last["shares_outstanding_undated_suppressed"]) is True
    assert pd.isna(last["shares_outstanding"])
    assert last["flow_method"] == "DATA_INSUFFICIENT"
    assert pd.isna(last["flow"])
    assert last["period_return"] == pytest.approx(0.10)


def test_undated_aum_change_requires_an_explicitly_dated_return_pair():
    history = pd.DataFrame([
        _row(
            "2026-08-18",
            aum=1000.0,
            nav=10.0,
            aum_as_of_explicit=False,
            nav_as_of_explicit=False,
            market_price_as_of_explicit=False,
        ),
        _row(
            "2026-08-19",
            aum=1100.0,
            nav=10.5,
            aum_as_of_explicit=False,
            nav_as_of_explicit=False,
            market_price_as_of_explicit=False,
        ),
    ])
    last = compute_daily_flows(history).iloc[-1]
    assert last["flow_method"] == "UNSCORABLE_UNDATED_AUM_WITHOUT_DATED_RETURN"
    assert pd.isna(last["flow"])


def test_market_price_fallback_return_includes_distribution():
    history = pd.DataFrame([
        _row("2026-08-18", aum=1000.0, market_price=10.0, nav=None),
        _row("2026-08-19", aum=1020.0, market_price=10.0, nav=None, distribution_per_share=0.10),
    ])
    last = compute_daily_flows(history).iloc[-1]
    assert last["period_return"] == pytest.approx(0.01)
    assert last["flow_method"] == "AUM_PERFORMANCE_ADJUSTED"
    assert last["flow"] == pytest.approx(10.0)


def test_rolling_rate_uses_shares_times_nav_when_aum_is_missing():
    dates = pd.bdate_range("2026-08-12", periods=6)
    shares = 100.0
    rows: list[dict] = []
    for index, date in enumerate(dates):
        if index:
            shares += 10.0
        rows.append(_row(date.date().isoformat(), aum=None, nav=10.0, shares_outstanding=shares, market_price=10.0))
    result = build_flow_computation(pd.DataFrame(rows), _cfg())
    last = result.observations.iloc[-1]
    assert last["flow_5d"] == pytest.approx(500.0)
    assert last["organic_flow_rate_5d"] == pytest.approx(0.5)
    assert last["flow_denominator_value"] == pytest.approx(1500.0)


def test_yfinance_snapshot_uses_market_date_and_dividend(monkeypatch):
    import yfinance as yf

    class FakeTicker:
        info = {
            "totalAssets": 1_000_000.0,
            "navPrice": 10.0,
            "sharesOutstanding": 100_000.0,
            "currency": "EUR",
        }

        def history(self, period: str, auto_adjust: bool):
            assert period == "5d"
            assert auto_adjust is False
            return pd.DataFrame(
                {"Close": [10.0, 10.2], "Dividends": [0.0, 0.15]},
                index=pd.to_datetime(["2026-08-18", "2026-08-19"]),
            )

    monkeypatch.setattr(yf, "Ticker", lambda ticker: FakeTicker())
    observation, failure = _yfinance_snapshot(pd.Series({"instrument_id": "A", "ticker": "A.PA", "currency": "EUR"}))
    assert failure is None
    assert observation is not None
    assert observation["as_of"] == "2026-08-19"
    assert observation["market_price"] == pytest.approx(10.2)
    assert observation["distribution_per_share"] == pytest.approx(0.15)
    assert observation["market_price_as_of_explicit"] is True
    assert observation["aum_as_of_explicit"] is False
    assert observation["shares_as_of_explicit"] is False


def test_temporal_integrity_flags_are_governed_without_reweighting():
    cfg = _cfg()
    anti = cfg["anti_false_signal"]
    assert anti["undated_shares_outstanding_for_flow_forbidden"] is True
    assert anti["undated_aum_requires_dated_return"] is True
    assert anti["market_price_return_includes_distribution"] is True
    assert anti["rolling_denominator_uses_aum_or_shares_nav"] is True
    assert cfg["governance"]["weights_changed_v21_16"] is False
    assert cfg["governance"]["thresholds_changed_v21_16"] is False
    assert cfg["governance"]["holdout_opened_v21_16"] is False
