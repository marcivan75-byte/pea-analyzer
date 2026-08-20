from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from v182.features.etf_mt_history_integrity import (
    assert_mt_reference_contract,
    sanitize_histories,
    score_snapshot_integrity,
)

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return json.loads((ROOT / "config" / "V20.8_ETF_MT_HIGH_PRECISION.json").read_text(encoding="utf-8"))


def _history(seed: int, sessions: int = 820) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=sessions)
    daily = 0.0005 + rng.normal(0.0, 0.006, sessions)
    close = 100.0 * np.exp(np.cumsum(daily))
    volume = np.maximum(10_000.0, 800_000.0 + rng.normal(0.0, 60_000.0, sessions))
    return pd.DataFrame({"Open": close, "High": close * 1.003, "Low": close * 0.997, "Close": close, "Volume": volume}, index=dates)


def test_union_index_padding_is_not_counted_as_history():
    real = _history(1, 300)
    padded = real.reindex(pd.bdate_range(real.index.min(), periods=820))
    clean = sanitize_histories({"ETF": padded})
    assert len(clean["ETF"]) == 300
    assert clean["ETF"].index.max() == real.index.max()


def test_reference_contract_requires_no_real_orders():
    cfg = _config()
    assert_mt_reference_contract(cfg)
    broken = json.loads(json.dumps(cfg))
    broken["status"] = "ACTIVE_REFERENCE_SCORING_WITH_ORDERS"
    with pytest.raises(ValueError, match="no-real-orders"):
        assert_mt_reference_contract(broken)


def test_integrity_wrapper_never_emits_buy_candidate():
    histories = {f"ISIN{i}": _history(i + 10) for i in range(8)}
    reference = pd.DataFrame({"isin": list(histories), "name": list(histories), "category": ["BROAD"] * 8})
    snapshot, summary = score_snapshot_integrity(histories, reference, _config())
    assert "BUY_CANDIDATE" not in set(snapshot["decision"].astype(str))
    assert summary["promotion_allowed"] is False
    assert summary["real_orders_allowed"] is False
    assert summary["history_session_policy"] == "OBSERVED_NUMERIC_CLOSE_ONLY"
