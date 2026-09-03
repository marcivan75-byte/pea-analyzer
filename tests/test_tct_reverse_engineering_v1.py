import numpy as np
import pandas as pd
import pytest

from v182.backtest.tct_reverse_engineering_v1 import (
    ReverseEngineeringConfig,
    append_only_history,
    asof_snapshot_features,
    build_catalyst_event_features,
    build_forward_labels,
    discover_patterns_discovery_only,
    prepare_research_matrix,
    run_eight_pass_audit,
    sanitize_feature_columns,
)


def _ohlcv(start=None, end=None, instruments=("A", "B", "C", "D")):
    dates = pd.DatetimeIndex([])
    for a, b in [
        ("2018-03-01", "2018-12-31"),
        ("2022-03-01", "2022-12-31"),
        ("2024-03-01", "2024-12-31"),
        ("2025-03-01", "2025-12-31"),
    ]:
        dates = dates.append(pd.bdate_range(a, b))
    rows = []
    rng = np.random.default_rng(7)
    for j, symbol in enumerate(instruments):
        n = len(dates)
        returns = rng.normal(0.0003, 0.015, n)
        for k in range(80 + j * 11, n, 180):
            if j < 2:
                returns[k:k+5] += 0.035
        close = 50 * np.exp(np.cumsum(returns))
        open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.002, n))
        high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.015, n))
        low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.015, n))
        volume = rng.integers(100_000, 1_000_000, n)
        rows.extend(
            {
                "date": date,
                "instrument_id": symbol,
                "open": op,
                "high": hi,
                "low": lo,
                "close": cl,
                "volume": vol,
            }
            for date, op, hi, lo, cl, vol in zip(dates, open_, high, low, close, volume)
        )
    return pd.DataFrame(rows)


def test_forward_labels_use_next_open_and_censor_incomplete_tail():
    data = pd.DataFrame(
        {
            "date": pd.bdate_range("2026-01-02", periods=7),
            "instrument_id": ["A"] * 7,
            "open": [100, 100, 100, 100, 100, 100, 100],
            "high": [100, 126, 101, 101, 101, 101, 101],
            "low": [99, 99, 99, 99, 99, 99, 99],
            "close": [100, 100, 100, 100, 100, 100, 100],
            "volume": [1000] * 7,
        }
    )
    cfg = ReverseEngineeringConfig(horizons=(5,), thresholds=(0.25,))
    labels = build_forward_labels(data, cfg)
    assert labels.loc[0, "entry_price_next_session"] == 100
    assert labels.loc[0, "label_hit_25_h5"] == 1
    assert labels.loc[0, "first_hit_25_h5"] == 1
    assert labels.loc[2:, "label_hit_25_h5"].isna().all()


def test_lookahead_feature_names_fail_closed():
    with pytest.raises(ValueError, match="LOOKAHEAD_FEATURE_REJECTED"):
        sanitize_feature_columns(["rsi14", "fwd_mfe_h20"])


def test_append_only_history_rejects_conflicting_same_timestamp():
    existing = pd.DataFrame(
        [{"instrument_id": "A", "observed_at_utc": "2026-01-01T18:00:00Z", "target_price": 100.0}]
    )
    incoming = pd.DataFrame(
        [{"instrument_id": "A", "observed_at_utc": "2026-01-01T18:00:00Z", "target_price": 110.0}]
    )
    with pytest.raises(ValueError, match="PIT_IMMUTABILITY_CONFLICT"):
        append_only_history(existing, incoming, key_cols=["instrument_id"])


def test_asof_features_never_use_future_consensus_and_use_session_lags():
    base = pd.DataFrame(
        {
            "instrument_id": ["A"] * 6,
            "date": pd.to_datetime([
                "2026-01-05", "2026-01-06", "2026-01-07",
                "2026-01-08", "2026-01-09", "2026-01-12",
            ]),
        }
    )
    history = pd.DataFrame(
        [
            {"instrument_id": "A", "observed_at_utc": "2026-01-05T00:00:00Z", "target_price": 100.0},
            {"instrument_id": "A", "observed_at_utc": "2026-01-09T00:00:00Z", "target_price": 120.0},
            {"instrument_id": "A", "observed_at_utc": "2026-01-13T00:00:00Z", "target_price": 999.0},
        ]
    )
    merged = asof_snapshot_features(base, history, value_cols=["target_price"], delta_windows=(5,))
    last = merged.iloc[-1]
    assert last["target_price"] == 120.0
    assert last["target_price_delta_5s"] == 20.0
    assert last["target_price_pct_delta_5s"] == pytest.approx(0.20)
    assert 999.0 not in set(merged["target_price"].dropna())


def test_split_purges_boundaries_and_discovery_does_not_open_holdout():
    data = _ohlcv()
    matrix, _ = prepare_research_matrix(data)
    assert "PURGED" in set(matrix["research_split"])
    matrix["mom5"] = matrix["ret_5d"] > 0.02
    matrix["rvol"] = matrix["rvol20"] > 1.1
    cfg = ReverseEngineeringConfig(min_support=10)
    patterns = discover_patterns_discovery_only(matrix, ["mom5", "rvol"], "label_hit_25_h20", cfg=cfg)
    discovery_baseline = matrix.loc[matrix["research_split"] == "DISCOVERY", "label_hit_25_h20"].mean()
    if not patterns.empty:
        assert np.allclose(patterns["baseline_rate"], discovery_baseline, equal_nan=True)


def test_catalyst_features_count_only_past_events():
    base = pd.DataFrame(
        {
            "instrument_id": ["A", "A"],
            "date": pd.to_datetime(["2026-01-10", "2026-01-20"]),
        }
    )
    events = pd.DataFrame(
        [
            {"instrument_id": "A", "observed_at_utc": "2026-01-09T08:00:00Z", "event_type": "GUIDANCE_UP"},
            {"instrument_id": "A", "observed_at_utc": "2026-01-15T08:00:00Z", "event_type": "GUIDANCE_UP"},
            {"instrument_id": "A", "observed_at_utc": "2026-01-21T08:00:00Z", "event_type": "GUIDANCE_UP"},
        ]
    )
    enriched, cols = build_catalyst_event_features(base, events, windows_days=(5, 20))
    assert "catalyst_guidance_up_count_5d" in cols
    assert enriched.loc[0, "catalyst_guidance_up_count_5d"] == 1
    assert enriched.loc[1, "catalyst_guidance_up_count_5d"] == 1
    assert enriched.loc[1, "catalyst_guidance_up_count_20d"] == 2


def test_eight_engineering_audits_pass_on_contract_fixture():
    data = _ohlcv()
    matrix, _ = prepare_research_matrix(data)
    matrix["mom5"] = matrix["ret_5d"] > 0.02
    matrix["rvol"] = matrix["rvol20"] > 1.1
    matrix["bo20"] = matrix["breakout_20d"].fillna(0).astype(bool)
    cfg = ReverseEngineeringConfig(min_support=10)
    factors = ["mom5", "rvol", "bo20"]
    patterns = discover_patterns_discovery_only(matrix, factors, "label_hit_25_h20", cfg=cfg)
    audit = run_eight_pass_audit(matrix, factors, patterns, cfg)
    assert len(audit) == 8
    assert set(audit["status"]) == {"PASS"}
