from __future__ import annotations

import numpy as np
import pandas as pd

from tools.v22_1_data.enrich_liquidity_pit import (
    MIN_ADV_20_EUR,
    compute_daily_liquidity,
    enrich_liquidity_pit,
    liquidity_report,
)


def _ohlcv() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=25)
    return pd.DataFrame(
        {
            "ticker": ["AAA.PA"] * len(dates),
            "date": dates,
            "close": [10.0] * len(dates),
            "volume": [100_000.0] * 20 + [50_000.0] * 5,
        }
    )


def test_volume_avg20_uses_only_prior_completed_sessions() -> None:
    daily = compute_daily_liquidity(_ohlcv())
    first_complete = daily.iloc[20]
    assert first_complete["volume_avg20"] == 100_000.0
    assert first_complete["adv_20_eur"] == 1_000_000.0


def test_enrichment_persists_governed_statuses() -> None:
    ohlcv = _ohlcv()
    dates = pd.bdate_range("2024-01-01", periods=25)
    pit = pd.DataFrame(
        {
            "ticker": ["AAA.PA", "AAA.PA"],
            "market_data_date": [dates[20], dates[24]],
            "signal_date": [dates[20], dates[24]],
        }
    )
    enriched = enrich_liquidity_pit(pit, ohlcv, min_adv_eur=MIN_ADV_20_EUR)
    assert enriched.loc[0, "liquidity_status"] == "ELIGIBLE"
    assert enriched.loc[1, "liquidity_status"] in {"ELIGIBLE", "BLOCK_ILLIQUID"}
    assert np.isfinite(enriched["adv_20_eur"]).all()


def test_missing_20_session_history_fails_closed_per_row() -> None:
    ohlcv = _ohlcv()
    first = pd.bdate_range("2024-01-01", periods=25)[5]
    pit = pd.DataFrame({"ticker": ["AAA.PA"], "market_data_date": [first], "signal_date": [first]})
    enriched = enrich_liquidity_pit(pit, ohlcv)
    assert enriched.loc[0, "liquidity_status"] == "BLOCK_DATA_LIQUIDITY"
    report = liquidity_report(enriched)
    assert report["known_liquidity_coverage"] == 0.0
