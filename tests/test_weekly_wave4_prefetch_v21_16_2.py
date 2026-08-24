from __future__ import annotations

import pandas as pd

from v182.reporting.weekly_unified_super_runner_v21_16_2 import (
    _rematerialize_wave4_observations,
)


def _row(isin: str, field: str, value, source: str = "yfinance_CACHE") -> dict:
    return {
        "universe": "ACTION",
        "isin": isin,
        "field": field,
        "value": value,
        "source": source,
        "collected_at": "2026-08-23T18:00:00+00:00",
        "as_of": "2026-08-23",
        "evidence_level": "C",
        "validation_status": "AUTO_MATCH",
    }


def test_wave4_prefetch_preserves_remote_observations_and_rebuilds_ratios():
    actions = pd.DataFrame(
        [
            {"isin": "FR0000000001", "last_close": 120.0},
            {"isin": "FR0000000002", "last_close": 50.0},
        ]
    )
    prefetched = [
        _row("FR0000000001", "trailing_eps_yf", 6.0),
        _row("FR0000000001", "forward_eps_yf", 8.0),
        _row("FR0000000001", "book_value_per_share_yf", 30.0),
        _row("FR0000000001", "sector_yf", "Industrials"),
        _row("FR0000000001", "per_ttm_yf", 999.0, "INTERNAL_OHLCV_X_YF_FUNDAMENTALS"),
        _row("FR0000000001", "per_forward_yf", 999.0, "INTERNAL_OHLCV_X_YF_FUNDAMENTALS"),
        _row("FR0000000001", "pb", 999.0, "INTERNAL_OHLCV_X_YF_FUNDAMENTALS"),
        _row("FR0000000002", "trailing_eps_yf", 0.0),
    ]

    result, count = _rematerialize_wave4_observations(actions, prefetched)

    remote = [row for row in result if row["field"] == "sector_yf"]
    assert remote == [_row("FR0000000001", "sector_yf", "Industrials")]

    ratios = {
        row["field"]: row
        for row in result
        if row["isin"] == "FR0000000001" and row["field"] in {"per_ttm_yf", "per_forward_yf", "pb"}
    }
    assert ratios["per_ttm_yf"]["value"] == 20.0
    assert ratios["per_forward_yf"]["value"] == 15.0
    assert ratios["pb"]["value"] == 4.0
    assert all(row["source"] == "INTERNAL_OHLCV_X_YF_FUNDAMENTALS" for row in ratios.values())
    assert all(row["evidence_level"] == "C" for row in ratios.values())
    assert count == 3

    # Zero EPS remains non-materializable exactly as the production positive-ratio rule.
    assert not any(
        row["isin"] == "FR0000000002" and row["field"] == "per_ttm_yf"
        for row in result
    )


def test_wave4_prefetch_no_last_close_keeps_remote_fields_without_ratio_fabrication():
    actions = pd.DataFrame([{"isin": "FR0000000001"}])
    prefetched = [
        _row("FR0000000001", "trailing_eps_yf", 6.0),
        _row("FR0000000001", "market_cap_yf", 1_000_000_000.0),
        _row("FR0000000001", "per_ttm_yf", 123.0, "INTERNAL_OHLCV_X_YF_FUNDAMENTALS"),
    ]

    result, count = _rematerialize_wave4_observations(actions, prefetched)

    assert count == 0
    assert any(row["field"] == "trailing_eps_yf" for row in result)
    assert any(row["field"] == "market_cap_yf" for row in result)
    assert not any(row["field"] in {"per_ttm_yf", "per_forward_yf", "pb"} for row in result)
