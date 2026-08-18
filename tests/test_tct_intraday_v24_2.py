from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.decision.tct_intraday_shadow_v24_2 import evaluate_intraday_session
from v182.features.tct_intraday_v24_2 import compute_intraday_features


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_2_0_INTRADAY_SHADOW.json").read_text(encoding="utf-8"))


def _history() -> pd.DataFrame:
    frames = []
    sessions = pd.bdate_range("2026-07-01", "2026-07-20")[:12]
    for day_number, day in enumerate(sessions):
        idx = pd.date_range(pd.Timestamp(day.date()) + pd.Timedelta(hours=9), periods=30, freq="5min")
        base = 100.0 + 0.10 * day_number
        close = base + np.linspace(0.0, 0.30, 30)
        volume = np.full(30, 1000.0)
        if day_number == 11:
            close[20:] += np.linspace(0.10, 2.0, 10)
            volume[20:] = 2500.0
        frames.append(
            pd.DataFrame(
                {
                    "open": close - 0.02,
                    "high": close + 0.05,
                    "low": close - 0.05,
                    "close": close,
                    "volume": volume,
                },
                index=idx,
            )
        )
    return pd.concat(frames)


def test_intraday_features_are_causal_against_future_bar_mutation():
    cfg = _cfg()
    raw = _history()
    first = compute_intraday_features(raw, cfg)
    session = first["session_date"].astype(str).max()
    session_index = first[first["session_date"].astype(str) == session].index
    cut = session_index[18]

    mutated = raw.copy()
    future = (mutated.index > cut) & (pd.Index(mutated.index.date).astype(str) == session)
    mutated.loc[future, "volume"] *= 50.0
    mutated.loc[future, "high"] += 20.0
    mutated.loc[future, "close"] += 10.0
    second = compute_intraday_features(mutated, cfg)

    cols = [
        "vwap",
        "rvol_slot",
        "prior_high",
        "opening_range_high",
        "range_expansion_ratio",
        "return_3bar",
    ]
    pd.testing.assert_frame_equal(first.loc[:cut, cols], second.loc[:cut, cols])


def test_opening_range_is_not_actionable_before_completion():
    features = compute_intraday_features(_history(), _cfg())
    session = features["session_date"].astype(str).min()
    first_session = features[features["session_date"].astype(str) == session]
    opening_bars = int(_cfg()["intraday_data"]["opening_range_bars"])
    assert not first_session.iloc[:opening_bars]["opening_range_ready"].astype(bool).any()
    assert first_session.iloc[opening_bars:]["opening_range_ready"].astype(bool).all()


def test_strong_synthetic_breakout_can_only_create_shadow_entry():
    cfg = _cfg()
    features = compute_intraday_features(_history(), cfg)
    session = features["session_date"].astype(str).max()
    result = evaluate_intraday_session(features, session, cfg)
    assert result.status == "CAUSAL_ENTRY_EVENT"
    assert result.shadow_state in {"ENTRY_READY_SHADOW", "ENTRY_STRONG_SHADOW"}
    assert result.setup in {"EXPLOSIVE_BREAKOUT", "BREAKOUT_RETEST", "OPENING_RANGE_BREAKOUT", "VWAP_RECLAIM"}
    assert result.entry_price is not None
    assert result.mfe_to_close_pct is not None
    assert result.mae_to_close_pct is not None


def test_v242_governance_has_zero_production_authority():
    cfg = _cfg()
    gov = cfg["governance"]
    assert cfg["status"] == "SHADOW_RESEARCH_ONLY"
    assert cfg["mode"] == "SHADOW_ONLY"
    assert gov["decision_influence"] == 0.0
    assert gov["score_influence"] == 0.0
    assert gov["sizing_execution_influence"] == 0.0
    assert gov["stop_loss_influence"] == 0.0
    assert gov["ct_influence"] == 0.0
    assert gov["real_orders_enabled"] is False
    assert gov["fixed_take_profit_enabled"] is False
    assert gov["fixed_stop_loss_enabled"] is False
    assert gov["holdout_locked"] is True
    assert cfg["signal_bridge"]["same_session_execution_forbidden"] is True
    assert cfg["signal_bridge"]["minimum_lag_sessions"] >= 1
    assert abs(sum(cfg["diagnostic_weights"].values()) - 1.0) < 1e-12
