from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from v182.reporting import etf_structure_refresh as module


def _obs(isin: str, field: str, age_days: int) -> dict:
    stamp = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    return {"isin": isin, "field": field, "collected_at": stamp, "as_of": stamp[:10]}


def test_fresh_source_isins_respects_source_family_age():
    replay = [
        _obs("FRESH", "fund_total_assets_eur_m", 20),
        _obs("STALE", "ter_pct", 80),
        _obs("OTHER", "share_class_inception_date", 10),
    ]
    fresh = module._fresh_source_isins(replay, module.STRUCTURAL_SOURCE_FIELDS, 62)
    assert "FRESH" in fresh
    assert "STALE" not in fresh
    assert "OTHER" not in fresh


def test_due_frame_keeps_only_expired_or_missing_isins():
    frame = pd.DataFrame({"isin": ["A", "B", "C"], "provider": ["X", "X", "X"]})
    due = module._due_frame(frame, {"A", "C"})
    assert due["isin"].tolist() == ["B"]


def test_monthly_portfolio_structure_policy_is_31_days():
    assert module.FUND_STRUCTURE_FIELDS
    # The source-family TTL is intentionally encoded in run() as 31 days and
    # mirrored by config/ETF_STRUCTURE_STATE_V21_15.json field max ages.
    fresh = module._fresh_source_isins(
        [_obs("ETF", "direct_sector_hhi", 30)], module.FUND_STRUCTURE_FIELDS, 31
    )
    assert fresh == {"ETF"}
