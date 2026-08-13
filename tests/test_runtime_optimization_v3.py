from __future__ import annotations

import sys
from types import ModuleType
import numpy as np
import pandas as pd

from v182.sources.gdelt_news import NewsScore, score_queries
from v182.sources.yfinance_bulk import _merge_history_frames, _overlap_rebase_detected
from v182.sources.yfinance_funds import collect_fund_structure


def test_incremental_merge_preserves_history_and_peer_values():
    columns=pd.MultiIndex.from_product([["AAA.PA","BBB.PA"],["Close"]])
    old=pd.DataFrame([[10.0,20.0],[11.0,21.0]],index=pd.date_range("2026-08-11",periods=2),columns=columns)
    fresh=pd.DataFrame([[11.5,np.nan],[12.0,22.0]],index=pd.date_range("2026-08-12",periods=2),columns=columns)
    merged=_merge_history_frames(old,fresh)
    assert merged.loc[pd.Timestamp("2026-08-11"),("AAA.PA","Close")]==10.0
    assert merged.loc[pd.Timestamp("2026-08-12"),("AAA.PA","Close")]==11.5
    assert merged.loc[pd.Timestamp("2026-08-12"),("BBB.PA","Close")]==21.0
    assert merged.loc[pd.Timestamp("2026-08-13"),("BBB.PA","Close")]==22.0


def test_adjusted_history_revision_triggers_rebuild_signal_but_latest_session_is_ignored():
    columns=pd.MultiIndex.from_product([["AAA.PA"],["Open","Close"]])
    idx=pd.date_range("2026-08-10",periods=4)
    old=pd.DataFrame([[10.0,10.2],[10.2,10.4],[10.4,10.6],[10.6,10.8]],index=idx,columns=columns)
    revised=old.copy(); revised.loc[idx[1],("AAA.PA","Close")]=5.2
    assert _overlap_rebase_detected(old,revised) is True
    current_only=old.copy(); current_only.loc[idx[-1],("AAA.PA","Close")]=11.1
    assert _overlap_rebase_detected(old,current_only) is False


def test_fund_structure_bounded_concurrency_preserves_same_fields(monkeypatch):
    fake_yf=ModuleType("yfinance")
    class Funds:
        def __init__(self,ticker):
            self.top_holdings=pd.DataFrame({"Holding Percent":[0.2,0.1]})
            self.sector_weightings={"Technology":0.6,"Health":0.4}
    class FakeTicker:
        def __init__(self,ticker):
            self.ticker=ticker; self.funds_data=Funds(ticker)
    fake_yf.Ticker=FakeTicker
    monkeypatch.setitem(sys.modules,"yfinance",fake_yf)
    observations,failures=collect_fund_structure(["BBB","AAA","AAA"],delay_seconds=0,max_workers=2)
    assert failures==[]
    per_ticker={}
    for row in observations:
        per_ticker.setdefault(row["ticker"],set()).add(row["field"])
    expected={"direct_top_holdings_concentration_pct","top_holdings_concentration_pct","direct_sector_hhi","direct_diversification_score","diversification_direct_score","top_holdings_observed_count"}
    assert per_ticker=={"AAA":expected,"BBB":expected}


def test_gdelt_batch_deduplicates_identical_queries(monkeypatch):
    import v182.sources.gdelt_news as gdelt
    calls=[]
    def fake_uncached(query,timespan,max_records,limiter=None):
        calls.append((query,timespan,max_records))
        return NewsScore(60.0,2,2,0,"GDELT"),None
    monkeypatch.setattr(gdelt,"_score_query_uncached",fake_uncached)
    result=score_queries(["same query","same query","other query"],timespan="2d",max_records=50,delay_seconds=0,max_workers=2)
    assert set(result)=={"same query","other query"}
    assert len(calls)==2
