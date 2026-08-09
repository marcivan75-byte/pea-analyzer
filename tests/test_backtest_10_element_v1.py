from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.backtest_optimizer import BacktestOptimizer, OptimizerConfig
from v182.backtest_optimizer.history import build_rolling_history

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "BACKTEST_10_ELEMENT_V1.json"


def test_master_config_contains_exactly_ten_independent_elements():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    elements = cfg["elements"]
    assert cfg["sequential"] is True
    assert cfg["production_weights_modified"] is False
    assert len(elements) == 10
    assert len({e["id"] for e in elements}) == 10
    assert [e["asset_class"] for e in elements].count("ETF") == 5
    assert [e["asset_class"] for e in elements].count("ACTION") == 5
    etf_short = next(e for e in elements if e["id"] == "ETF_SHORT")
    assert etf_short["target_direction"] == -1
    action_horizons = [e["horizon_days"] for e in elements if e["asset_class"] == "ACTION"]
    assert action_horizons == [28, 91, 182, 365, 730]


def test_rolling_history_keeps_labels_aligned(tmp_path: Path):
    raw = tmp_path / "raw"
    out = tmp_path / "out"
    for date, a_price, b_price in [("2026-01-01", 100.0, 200.0), ("2026-01-29", 110.0, 180.0)]:
        d = raw / f"{date}__123"
        d.mkdir(parents=True)
        pd.DataFrame({
            "canonical_isin": ["FR0000000001", "FR0000000002"],
            "canonical_last_close": [a_price, b_price],
            "score_momentum_100": [80, 20],
            "score_quality_100": [60, 60],
        }).to_csv(d / "V20.4_GITOK_ACTIONS_3609_DECISIONS.csv", sep=";", index=False)
        pd.DataFrame({
            "isin": ["FR0010000001", "FR0010000002"],
            "last_close": [a_price / 10.0, b_price / 10.0],
            "perf_1m_pct": [5, -5],
        }).to_csv(d / "V20.7_ETF102_COMMITTEE.csv", sep=";", index=False)

    audit = build_rolling_history(raw, None, out, CONFIG)
    assert audit["asset_classes"]["ACTION"]["snapshots"] == 2
    hist = pd.read_parquet(out / "ACTION_HISTORY.parquet")
    first = hist[hist["__snapshot_date"].eq(pd.Timestamp("2026-01-01"))].set_index("__instrument_id")
    assert abs(float(first.loc["FR0000000001", "__forward_return_28d"]) - 0.10) < 1e-12
    assert abs(float(first.loc["FR0000000002", "__forward_return_28d"]) + 0.10) < 1e-12


def test_observed_weight_renormalization_runs_without_neutral_fill():
    rng = np.random.default_rng(7)
    rows = []
    dates = pd.date_range("2025-01-01", periods=14, freq="14D")
    for d in dates:
        for i in range(30):
            f1 = float(rng.uniform(0, 100))
            f2 = np.nan if i % 3 == 0 else float(rng.uniform(0, 100))
            rows.append({
                "__snapshot_date": d,
                "__instrument_id": f"X{i:02d}",
                "__forward_return": (f1 - 50.0) / 1000.0 + rng.normal(0, 0.01),
                "f1": f1,
                "f2": f2,
            })
    df = pd.DataFrame(rows)
    cfg = OptimizerConfig(
        top_k=5,
        candidate_count=80,
        min_snapshots=10,
        min_test_snapshots=3,
        min_instruments_per_snapshot=20,
        max_single_weight=0.80,
        include_default_features=False,
        missing_feature_policy="RENORMALIZE_OBSERVED",
        min_feature_weight_coverage=0.55,
        feature_overrides={
            "f1": {"candidates": ["f1"], "baseline": 0.60, "optional": False},
            "f2": {"candidates": ["f2"], "baseline": 0.40, "optional": False},
        },
        min_oos_improvement=-1.0,
        max_drawdown_worsening=1.0,
    )
    result = BacktestOptimizer(cfg).optimize(df)
    assert result.status in {"ROBUST_RECOMMENDATION", "NO_ROBUST_IMPROVEMENT"}
    assert result.audit["missing_feature_policy"] == "RENORMALIZE_OBSERVED"
    assert result.audit["production_weights_modified"] is False
