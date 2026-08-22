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


def test_portfolio_summary_counts_each_active_isin_once_across_horizons():
    idx = pd.date_range("2025-01-01", periods=260, freq="B")
    market = pd.Series(np.sin(np.linspace(0, 20, len(idx))) / 100.0, index=idx)
    returns = {"A": market, "B": market * 0.7}
    rows = pd.DataFrame(
        [
            {
                "isin": "A",
                "horizon": "CT",
                "decision": "BUY_CANDIDATE",
                "score": 88.0,
                "risk_engine_tags": "TECH",
                "risk_beta_252d": 1.2,
                "risk_downside_beta_252d": 1.3,
            },
            {
                "isin": "A",
                "horizon": "MT",
                "decision": "BUY_CANDIDATE",
                "score": 82.0,
                "risk_engine_tags": "TECH",
                "risk_beta_252d": 1.2,
                "risk_downside_beta_252d": 1.3,
            },
            {
                "isin": "B",
                "horizon": "MT",
                "decision": "HOLD",
                "score": 80.0,
                "risk_engine_tags": "HEALTH",
                "risk_beta_252d": 0.8,
                "risk_downside_beta_252d": 0.9,
            },
        ]
    )

    summary = beta_portfolio.portfolio_summary(rows, returns, market, [-10.0])

    assert summary["analysis_universe"] == "UNIQUE_ACTIVE_COMMITTEE_ISINS_NOT_HELD_PORTFOLIO"
    assert summary["is_real_portfolio"] is False
    assert summary["real_portfolio_fit_status"] == "NOT_AVAILABLE_NO_PORTFOLIO_INPUT"
    assert summary["source_rows_before_isin_dedup"] == 3
    assert summary["unique_isins"] == 2
    assert summary["duplicate_horizon_rows_removed"] == 1
    assert summary["active_rows"] == 2
    assert summary["weight_method"] == "EQUAL_WEIGHT_UNIQUE_ISIN_DIAGNOSTIC"
    assert round(summary["portfolio_beta_252d"], 6) == 1.0
    assert round(summary["portfolio_downside_beta_252d"], 6) == 1.1
