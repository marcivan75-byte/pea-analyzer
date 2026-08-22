import numpy as np
import pandas as pd

from v182.risk import beta_portfolio


def test_economic_overlap_reuses_duplicate_horizon_work(monkeypatch):
    idx = pd.date_range("2025-01-01", periods=150, freq="B")
    base = pd.Series(np.linspace(-0.02, 0.02, len(idx)), index=idx)
    returns_by_isin = {
        "A": base,
        "B": base.copy(),
        "C": -base,
    }
    rows = pd.DataFrame(
        [
            {"isin": "A", "horizon": "CT", "decision": "BUY_CANDIDATE", "risk_engine_tags": "TECH|AI"},
            {"isin": "A", "horizon": "MT", "decision": "WATCH", "risk_engine_tags": "TECH|AI"},
            {"isin": "B", "horizon": "CT", "decision": "HOLD", "risk_engine_tags": "TECH|AI"},
            {"isin": "B", "horizon": "MT", "decision": "WATCH", "risk_engine_tags": "TECH|AI"},
            {"isin": "C", "horizon": "MT", "decision": "WATCH", "risk_engine_tags": "BANK"},
        ]
    )

    original_concat = beta_portfolio.pd.concat
    calls = 0

    def counted_concat(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_concat(*args, **kwargs)

    monkeypatch.setattr(beta_portfolio.pd, "concat", counted_concat)
    scores = beta_portfolio.economic_overlap_scores(rows, returns_by_isin)

    assert scores[0] == scores[1] == 100.0
    assert scores[2] == scores[3] == 100.0
    assert scores[4] == 0.0
    assert calls == 3


def test_economic_overlap_keeps_missing_history_semantics():
    idx = pd.date_range("2025-01-01", periods=150, freq="B")
    base = pd.Series(np.linspace(-0.01, 0.01, len(idx)), index=idx)
    rows = pd.DataFrame(
        [
            {"isin": "A", "decision": "BUY", "risk_engine_tags": "TECH"},
            {"isin": "MISSING", "decision": "WATCH", "risk_engine_tags": "TECH"},
        ]
    )
    scores = beta_portfolio.economic_overlap_scores(rows, {"A": base})
    assert scores == [0.0, None]
