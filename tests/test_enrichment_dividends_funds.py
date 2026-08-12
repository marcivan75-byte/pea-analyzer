from datetime import datetime, timezone
from types import SimpleNamespace
import sys
import pandas as pd

from v182.features.ohlcv_features import _dividend_features
from v182.sources.yfinance_bulk import _resolve_actions_requested
from v182.sources.yfinance_funds import (
    top_holdings_concentration_pct, sector_hhi, diversification_score, collect_fund_structure,
)
from v182.sources.yfinance_info import _future_earnings_fields


def test_dividend_features_use_observed_cash_windows_only():
    idx=pd.date_range("2022-08-01","2026-08-12",freq="D")
    frame=pd.DataFrame(index=idx,data={"Dividends":0.0})
    frame.loc[pd.Timestamp("2023-03-15"),"Dividends"]=2.0
    frame.loc[pd.Timestamp("2026-03-15"),"Dividends"]=4.0
    out=_dividend_features(frame)
    assert out["distribution_policy"] == 1.0
    assert out["dividend_ttm"] == 4.0
    expected=((4.0/2.0)**(1/3)-1)*100.0
    assert abs(out["dividend_cagr_3y"]-expected) < 1e-5


def test_no_dividend_history_does_not_invent_cagr():
    idx=pd.date_range("2022-08-01","2026-08-12",freq="D")
    frame=pd.DataFrame(index=idx,data={"Dividends":0.0})
    out=_dividend_features(frame)
    assert out["distribution_policy"] == 0.0
    assert out["dividend_cagr_3y"] is None


def test_corporate_actions_are_action_cache_only_by_default():
    assert _resolve_actions_requested("/tmp/cache/actions",None) is True
    assert _resolve_actions_requested("/tmp/cache/etf",None) is False
    assert _resolve_actions_requested("/tmp/cache/etf",True) is True
    assert _resolve_actions_requested("/tmp/cache/actions",False) is False


def test_etf_structure_keeps_concentration_separate_from_hhi_score():
    top=pd.DataFrame({"Holding Percent":[0.10,0.20,0.05]})
    concentration=top_holdings_concentration_pct(top)
    hhi=sector_hhi({"technology":0.5,"finance":0.3,"health":0.2})
    assert concentration == 35.0
    assert abs(hhi-0.38) < 1e-8
    assert diversification_score(hhi) == 62.0


def test_diversification_requires_observed_sector_hhi():
    assert diversification_score(None) is None


def test_top_holdings_row_count_is_not_total_holdings_count(monkeypatch):
    top=pd.DataFrame({"Holding Percent":[0.10,0.20,0.05]})
    funds=SimpleNamespace(top_holdings=top,sector_weightings={"technology":0.5,"finance":0.3,"health":0.2})
    fake_ticker=lambda _ticker: SimpleNamespace(funds_data=funds)
    monkeypatch.setitem(sys.modules,"yfinance",SimpleNamespace(Ticker=fake_ticker))
    observations,failures=collect_fund_structure(["TEST.PA"],delay_seconds=0)
    assert failures == []
    fields={row["field"] for row in observations}
    assert "top_holdings_observed_count" in fields
    assert "direct_holdings_count" not in fields


def test_earnings_calendar_exposes_days_and_flags_not_catalyst_score():
    now=datetime(2026,8,12,8,0,tzinfo=timezone.utc).timestamp()
    info={"earningsTimestampStart":now+5*86400}
    out=_future_earnings_fields(info,now_ts=now)
    assert out["days_to_earnings"] == 5.0
    assert out["earnings_within_7d_flag"] == 1.0
    assert out["earnings_within_30d_flag"] == 1.0
    assert "earnings_catalyst_score" not in out
