from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.features.etf_mt_v2081 import (
    build_equal_weight_market_proxy,
    compute_raw_features,
    score_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]


def _history(seed: int, drift: float = 0.00035, sessions: int = 820) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=sessions)
    daily = drift + rng.normal(0.0, 0.006 + seed * 0.00015, sessions)
    close = 100.0 * np.exp(np.cumsum(daily))
    volume = 750_000.0 + seed * 60_000.0 + rng.normal(0.0, 80_000.0, sessions)
    volume = np.maximum(volume, 10_000.0)
    return pd.DataFrame(
        {
            "Open": close * (1.0 + rng.normal(0.0, 0.001, sessions)),
            "High": close * 1.003,
            "Low": close * 0.997,
            "Close": close,
            "Volume": volume,
        },
        index=dates,
    )


def _config() -> dict:
    return json.loads((ROOT / "config" / "V20.8_ETF_MT_HIGH_PRECISION.json").read_text(encoding="utf-8"))


def test_compute_all_38_dynamic_criteria():
    histories = {f"ETF{i}": _history(i) for i in range(1, 7)}
    proxy = build_equal_weight_market_proxy(histories)
    features = compute_raw_features(histories["ETF1"], proxy)
    expected = set(_config()["dynamic_criteria"])
    assert len(expected) == 38
    assert set(features) == expected
    assert all(np.isfinite(value) for value in features.values())


def test_snapshot_scoring_gates_and_top2():
    histories = {f"ISIN{i}": _history(i, drift=0.00025 + i * 0.00005) for i in range(1, 9)}
    reference = pd.DataFrame(
        {
            "isin": list(histories),
            "name": [f"ETF {i}" for i in range(1, 9)],
            "category": ["EUROPE" if i < 5 else "WORLD" for i in range(1, 9)],
            "official_benchmark": [f"BENCH_{i % 3}" for i in range(1, 9)],
        }
    )
    snapshot, summary = score_snapshot(histories, reference, _config())

    assert len(snapshot) == 8
    assert snapshot["criteria_complete"].all()
    assert snapshot["score_raw"].between(0, 100).all()
    assert snapshot["score_rank_pct"].between(0, 100).all()
    assert snapshot["score_final"].between(0, 100).all()
    assert len(summary["selected"]) <= 2
    assert summary["top_n"] == 2
    assert summary["selection_threshold"] == 82.0
    if summary["selected"]:
        assert all(item["score_final"] >= 82.0 for item in summary["selected"])


def test_insufficient_history_is_blocked_not_renormalised():
    long_histories = {f"ISIN{i}": _history(i) for i in range(1, 5)}
    long_histories["SHORT"] = _history(10, sessions=300)
    reference = pd.DataFrame(
        {
            "isin": list(long_histories),
            "name": list(long_histories),
            "category": ["BROAD"] * len(long_histories),
        }
    )
    snapshot, _ = score_snapshot(long_histories, reference, _config())
    short = snapshot.loc[snapshot["instrument_id"] == "SHORT"].iloc[0]
    assert not bool(short["criteria_complete"])
    assert short["decision"] == "BLOCK_DATA"
    assert short["missing_criteria_count"] > 0
