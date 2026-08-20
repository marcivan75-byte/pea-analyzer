from __future__ import annotations

import pandas as pd
import pytest

from v182.sources.etf_fund_flows import (
    _blackrock_official_snapshot,
    _parse_blackrock_field,
)


def _row() -> pd.Series:
    return pd.Series(
        {
            "instrument_id": "US_TICKER:TEST",
            "ticker": "TEST",
            "name": "Test iShares ETF",
            "universe": "EXTERNAL",
            "asset_class": "ETF",
            "economic_family": "WORLD",
            "region": "US",
            "sector_or_theme": "",
            "benchmark": "MSCI World",
            "provider": "iShares",
            "currency": "USD",
            "is_pea": False,
            "is_synthetic": False,
            "is_inverse_or_leveraged": False,
            "official_adapter": "BLACKROCK_HTML",
            "official_url": "https://example.test/ishares",
        }
    )


class _FakeResponse:
    def __init__(self, html: str):
        self.text = html

    def raise_for_status(self) -> None:
        return None


def test_blackrock_field_does_not_inherit_unrelated_page_date():
    text = (
        "NAV as of 19/Aug/2026 USD 10.00 "
        "Net Assets of Fund USD 1,000,000"
    )
    aum, aum_date = _parse_blackrock_field(text, "aum")
    nav, nav_date = _parse_blackrock_field(text, "nav")

    assert aum == pytest.approx(1_000_000.0)
    assert aum_date is None
    assert nav == pytest.approx(10.0)
    assert nav_date == "2026-08-19"


def test_blackrock_same_date_fields_are_all_explicit():
    text = (
        "Net Assets of Fund as of 19/Aug/2026 USD 1,000,000 "
        "Shares Outstanding as of 19/Aug/2026 100,000 "
        "NAV as of 19/Aug/2026 USD 10.00"
    )
    for field, expected in (
        ("aum", 1_000_000.0),
        ("shares_outstanding", 100_000.0),
        ("nav", 10.0),
    ):
        value, date = _parse_blackrock_field(text, field)
        assert value == pytest.approx(expected)
        assert date == "2026-08-19"


def test_blackrock_conflicting_aum_date_is_dropped(monkeypatch):
    html = """
    <html><body>
      <div>Net Assets of Fund as of 18/Aug/2026 USD 1,000,000</div>
      <div>Shares Outstanding as of 19/Aug/2026 100,000</div>
      <div>NAV as of 19/Aug/2026 USD 10.00</div>
      <div>Base Currency USD</div>
    </body></html>
    """
    monkeypatch.setattr(
        "v182.sources.etf_fund_flows.requests.get",
        lambda *args, **kwargs: _FakeResponse(html),
    )

    observation, failure = _blackrock_official_snapshot(_row())

    assert failure is None
    assert observation is not None
    assert observation["as_of"] == "2026-08-19"
    assert observation["aum"] is None
    assert observation["nav"] == pytest.approx(10.0)
    assert observation["shares_outstanding"] == pytest.approx(100_000.0)
    assert observation["aum_as_of_explicit"] is False
    assert observation["nav_as_of_explicit"] is True
    assert observation["shares_as_of_explicit"] is True
    assert observation["official_field_date_conflict"] is True
    assert observation["official_conflicting_fields_dropped"] == "aum"


def test_blackrock_aum_and_nav_date_can_outweigh_shares_date(monkeypatch):
    html = """
    <html><body>
      <div>Net Assets of Fund as of 19/Aug/2026 USD 1,000,000</div>
      <div>NAV as of 19/Aug/2026 USD 10.00</div>
      <div>Shares Outstanding as of 18/Aug/2026 100,000</div>
      <div>Base Currency USD</div>
    </body></html>
    """
    monkeypatch.setattr(
        "v182.sources.etf_fund_flows.requests.get",
        lambda *args, **kwargs: _FakeResponse(html),
    )

    observation, failure = _blackrock_official_snapshot(_row())

    assert failure is None
    assert observation is not None
    assert observation["as_of"] == "2026-08-19"
    assert observation["aum"] == pytest.approx(1_000_000.0)
    assert observation["nav"] == pytest.approx(10.0)
    assert observation["shares_outstanding"] is None
    assert observation["aum_as_of_explicit"] is True
    assert observation["nav_as_of_explicit"] is True
    assert observation["shares_as_of_explicit"] is False
    assert observation["official_field_date_conflict"] is True
    assert observation["official_conflicting_fields_dropped"] == "shares_outstanding"


def test_blackrock_undated_fields_remain_non_explicit(monkeypatch):
    html = """
    <html><body>
      <div>Net Assets of Fund USD 1,000,000</div>
      <div>Shares Outstanding 100,000</div>
      <div>NAV USD 10.00</div>
      <div>Base Currency USD</div>
    </body></html>
    """
    monkeypatch.setattr(
        "v182.sources.etf_fund_flows.requests.get",
        lambda *args, **kwargs: _FakeResponse(html),
    )

    observation, failure = _blackrock_official_snapshot(_row())

    assert failure is None
    assert observation is not None
    assert observation["aum"] == pytest.approx(1_000_000.0)
    assert observation["shares_outstanding"] == pytest.approx(100_000.0)
    assert observation["nav"] == pytest.approx(10.0)
    assert observation["aum_as_of_explicit"] is False
    assert observation["nav_as_of_explicit"] is False
    assert observation["shares_as_of_explicit"] is False
    assert observation["official_field_date_conflict"] is False
