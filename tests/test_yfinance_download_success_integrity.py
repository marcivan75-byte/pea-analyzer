from __future__ import annotations

import numpy as np
import pandas as pd

from v182.sources.yfinance_bulk import _clear_history_cache, _contains_ticker


def test_multiindex_ticker_with_all_nan_prices_is_not_successful():
    columns=pd.MultiIndex.from_product([["GOOD.PA","DEAD.PA"],["Open","High","Low","Close","Volume"]])
    frame=pd.DataFrame(np.nan,index=pd.date_range("2026-08-10",periods=2),columns=columns)
    frame.loc[:,("GOOD.PA","Close")]=[10.0,10.5]
    assert _contains_ticker(frame,"GOOD.PA") is True
    assert _contains_ticker(frame,"DEAD.PA") is False


def test_multiindex_is_detected_when_ticker_is_second_level():
    columns=pd.MultiIndex.from_product([["Open","Close"],["AAA.PA","BBB.PA"]])
    frame=pd.DataFrame(np.nan,index=pd.date_range("2026-08-10",periods=2),columns=columns)
    frame.loc[:,("Close","BBB.PA")]=[20.0,20.2]
    assert _contains_ticker(frame,"AAA.PA") is False
    assert _contains_ticker(frame,"BBB.PA") is True


def test_single_ticker_requires_real_ohlc_not_only_nonprice_data():
    idx=pd.date_range("2026-08-10",periods=2)
    price=pd.DataFrame({"Open":[np.nan,1.0],"Close":[np.nan,1.1],"Volume":[0,100]},index=idx)
    only_dividends=pd.DataFrame({"Dividends":[0.0,0.5]},index=idx)
    assert _contains_ticker(price,"ANY") is True
    assert _contains_ticker(only_dividends,"ANY") is False


def test_clear_history_cache_removes_batches_retries_and_manifest_only(tmp_path):
    stale_batch=tmp_path/"history_00000.parquet"
    stale_retry=tmp_path/"history_retry_00000_000.parquet"
    stale_manifest=tmp_path/"history_manifest.json"
    unrelated=tmp_path/"keep_me.txt"
    for path in (stale_batch,stale_retry,stale_manifest,unrelated):
        path.write_text("stale",encoding="utf-8")

    _clear_history_cache(tmp_path)

    assert not stale_batch.exists()
    assert not stale_retry.exists()
    assert not stale_manifest.exists()
    assert unrelated.exists()
