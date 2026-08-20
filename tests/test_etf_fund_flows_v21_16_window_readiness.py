from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.features.etf_fund_flows_v1 import build_flow_computation

ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ETF_FUND_FLOW_V1_SHADOW.json").read_text(encoding="utf-8"))


def _row(date: str, *, confidence: str = "A", shares: float = 10_000.0) -> dict:
    nav = 100.0
    return {
        "instrument_id": "A",
        "as_of": date,
        "name": "A",
        "universe": "EXTERNAL",
        "asset_class": "ETF",
        "economic_family": "WORLD",
        "region": "US",
        "sector_or_theme": "TECHNOLOGY",
        "source": "issuer",
        "source_type": "ISSUER_OFFICIAL",
        "confidence": confidence,
        "source_priority": 100,
        "aum": shares * nav,
        "nav": nav,
        "shares_outstanding": shares,
        "market_price": nav,
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


def _history(dates: pd.DatetimeIndex, bad_index: int) -> pd.DataFrame:
    rows: list[dict] = []
    shares = 10_000.0
    for index, date in enumerate(dates):
        if index:
            shares += 10.0
        rows.append(
            _row(
                date.date().isoformat(),
                confidence="D" if index == bad_index else "A",
                shares=shares,
            )
        )
    return pd.DataFrame(rows)


def test_cumulative_20_plus_does_not_score_if_current_20_flow_window_has_gap():
    dates = pd.bdate_range(end="2026-08-19", periods=30)
    result = build_flow_computation(_history(dates, bad_index=10), _cfg())
    instrument = result.instruments.iloc[0]

    assert int(instrument["flow_observations"]) >= 20
    assert pd.isna(instrument["organic_flow_rate_20d"])
    assert bool(instrument["current_20d_window_complete"]) is False
    assert pd.isna(instrument["efs_shadow"])
    assert instrument["efs_readiness"] == "DATA_INSUFFICIENT_CURRENT_20D_WINDOW"
    assert result.rotations.empty
    assert result.diagnostics["current_20d_window_incomplete"] == 1


def test_cumulative_60_plus_with_complete_20d_but_gapped_60d_is_not_mature():
    dates = pd.bdate_range(end="2026-08-19", periods=70)
    result = build_flow_computation(_history(dates, bad_index=15), _cfg())
    instrument = result.instruments.iloc[0]

    assert int(instrument["flow_observations"]) >= 60
    assert pd.notna(instrument["organic_flow_rate_20d"])
    assert pd.isna(instrument["organic_flow_rate_60d"])
    assert bool(instrument["current_20d_window_complete"]) is True
    assert bool(instrument["current_60d_window_complete"]) is False
    assert pd.notna(instrument["efs_shadow"])
    assert instrument["efs_readiness"] == "PRELIMINARY_GAPPED_60_PLUS"
    assert result.diagnostics["mature_60d_window_incomplete"] == 1


def test_config_declares_current_window_readiness_without_reweighting():
    cfg = _cfg()
    assert cfg["anti_false_signal"]["current_window_required_for_readiness"] is True
    assert cfg["preliminary_score_min_observations"] == 20
    assert cfg["mature_score_min_observations"] == 60
    assert sum(cfg["score_weights"].values()) == 1.0
    assert cfg["governance"]["weights_changed_v21_16"] is False
    assert cfg["governance"]["thresholds_changed_v21_16"] is False
    assert cfg["governance"]["holdout_opened_v21_16"] is False
