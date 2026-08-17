import math

import numpy as np
import pandas as pd

from v182.risk import beta_correlation_engine as engine
from v182.risk.beta_metrics import jaccard
from v182.risk.beta_portfolio import economic_overlap_scores, portfolio_summary


ACTIVE_DECISIONS = {"BUY", "BUY_CANDIDATE", "HOLD"}


def _naive_overlap(rows: pd.DataFrame, returns_by_isin: dict[str, pd.Series]) -> list[float | None]:
    active = set(rows.loc[rows["decision"].astype(str).str.upper().isin(ACTIVE_DECISIONS), "isin"].astype(str))
    scores: list[float | None] = []
    for _, row in rows.iterrows():
        isin = str(row.get("isin") or "")
        current = returns_by_isin.get(isin)
        if current is None:
            scores.append(None)
            continue
        candidates: list[float] = []
        current_tags = str(row.get("risk_engine_tags") or "").split("|")
        for other_idx, other in rows.iterrows():
            other_isin = str(other.get("isin") or "")
            if other_isin == isin or other_isin not in active:
                continue
            other_returns = returns_by_isin.get(other_isin)
            if other_returns is None:
                continue
            pair = pd.concat([current, other_returns], axis=1).dropna().tail(126)
            if len(pair) < 40:
                continue
            corr = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
            if not math.isfinite(corr):
                continue
            corr_component = max(0.0, min(1.0, corr))
            other_tags = str(rows.at[other_idx, "risk_engine_tags"] or "").split("|")
            tag_component = jaccard(current_tags, other_tags)
            candidates.append(100.0 * (0.70 * corr_component + 0.30 * tag_component))
        scores.append(round(max(candidates), 4) if candidates else 0.0)
    return scores


def _naive_pair_means(rows: pd.DataFrame, returns_by_isin: dict[str, pd.Series], benchmark: pd.Series):
    active = rows[rows["decision"].astype(str).str.upper().isin(ACTIVE_DECISIONS)].copy()
    active = active[active["isin"].astype(str).isin(returns_by_isin)]
    pair_corrs: list[float] = []
    stress_corrs: list[float] = []
    active_isins = active["isin"].astype(str).tolist()
    stress_dates = benchmark[benchmark <= benchmark.quantile(0.10)].index
    for pos, left in enumerate(active_isins):
        for right in active_isins[pos + 1 :]:
            pair = pd.concat([returns_by_isin[left], returns_by_isin[right]], axis=1).dropna().tail(252)
            if len(pair) >= 40:
                value = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
                if math.isfinite(value):
                    pair_corrs.append(value)
            stress = pd.concat([returns_by_isin[left], returns_by_isin[right]], axis=1).dropna()
            stress = stress.loc[stress.index.intersection(stress_dates)]
            if len(stress) >= 12:
                value = float(stress.iloc[:, 0].corr(stress.iloc[:, 1]))
                if math.isfinite(value):
                    stress_corrs.append(value)
    mean_corr = float(np.mean(pair_corrs)) if pair_corrs else None
    mean_stress = float(np.mean(stress_corrs)) if stress_corrs else None
    return mean_corr, mean_stress


def _returns(seed: int, benchmark: pd.Series, beta: float, noise: float = 0.002) -> pd.Series:
    rng = np.random.default_rng(seed)
    return beta * benchmark + pd.Series(rng.normal(0.0, noise, len(benchmark)), index=benchmark.index)


def _prices(returns: pd.Series, start: float = 100.0) -> pd.Series:
    return start * (1.0 + returns).cumprod()


def test_overlap_cache_is_exact_with_duplicate_horizons_and_tag_variants():
    idx = pd.date_range("2025-01-02", periods=180, freq="B")
    rng = np.random.default_rng(12)
    benchmark = pd.Series(rng.normal(0.0002, 0.01, len(idx)), index=idx)
    returns = {
        "A": _returns(1, benchmark, 1.2),
        "B": _returns(2, benchmark, 0.8),
        "C": _returns(3, benchmark, -0.4),
    }
    rows = pd.DataFrame(
        [
            {"isin": "A", "decision": "HOLD", "risk_engine_tags": "AI|SEMIS"},
            {"isin": "A", "decision": "HOLD", "risk_engine_tags": "AI|SEMIS"},
            {"isin": "B", "decision": "BUY_CANDIDATE", "risk_engine_tags": "AI|SOFTWARE"},
            {"isin": "B", "decision": "HOLD", "risk_engine_tags": "SOFTWARE"},
            {"isin": "C", "decision": "WATCH", "risk_engine_tags": "ENERGY"},
        ]
    )
    expected = _naive_overlap(rows, returns)
    actual = economic_overlap_scores(rows, returns)
    assert actual == expected


def test_portfolio_pair_means_preserve_original_row_pair_multiplicity_exactly():
    idx = pd.date_range("2024-01-02", periods=320, freq="B")
    rng = np.random.default_rng(33)
    benchmark = pd.Series(rng.normal(0.0001, 0.012, len(idx)), index=idx)
    returns = {
        "A": _returns(10, benchmark, 1.1),
        "B": _returns(11, benchmark, 0.7),
        "C": _returns(12, benchmark, -0.2),
    }
    rows = pd.DataFrame(
        [
            {"isin": "A", "decision": "HOLD", "risk_engine_tags": "AI", "risk_beta_252d": 1.1, "risk_downside_beta_252d": 1.2},
            {"isin": "A", "decision": "HOLD", "risk_engine_tags": "AI", "risk_beta_252d": 1.1, "risk_downside_beta_252d": 1.2},
            {"isin": "A", "decision": "BUY_CANDIDATE", "risk_engine_tags": "AI", "risk_beta_252d": 1.1, "risk_downside_beta_252d": 1.2},
            {"isin": "B", "decision": "HOLD", "risk_engine_tags": "SOFTWARE", "risk_beta_252d": 0.7, "risk_downside_beta_252d": 0.8},
            {"isin": "B", "decision": "HOLD", "risk_engine_tags": "SOFTWARE", "risk_beta_252d": 0.7, "risk_downside_beta_252d": 0.8},
            {"isin": "C", "decision": "WATCH", "risk_engine_tags": "ENERGY", "risk_beta_252d": -0.2, "risk_downside_beta_252d": -0.1},
        ]
    )
    expected_corr, expected_stress = _naive_pair_means(rows, returns, benchmark)
    summary = portfolio_summary(rows, returns, benchmark, [-5, -10])
    assert summary["active_rows"] == 5
    assert summary["mean_pair_correlation_252d"] == round(expected_corr, 6)
    assert summary["mean_stress_pair_correlation"] == round(expected_stress, 6)


def test_overlay_computes_beta_and_price_returns_once_per_unique_isin(monkeypatch):
    idx = pd.date_range("2025-01-02", periods=90, freq="B")
    rng = np.random.default_rng(77)
    benchmark = pd.Series(rng.normal(0.0002, 0.01, len(idx)), index=idx)
    decisions = pd.DataFrame(
        [
            {"asset_class": "ACTION", "horizon": "CT", "isin": "A", "name": "Alpha", "decision": "HOLD", "score": 70.0},
            {"asset_class": "ACTION", "horizon": "MT", "isin": "A", "name": "Alpha", "decision": "HOLD", "score": 72.0},
            {"asset_class": "ACTION", "horizon": "LT", "isin": "A", "name": "Alpha", "decision": "WATCH", "score": 74.0},
            {"asset_class": "ACTION", "horizon": "CT", "isin": "B", "name": "Beta", "decision": "HOLD", "score": 68.0},
            {"asset_class": "ACTION", "horizon": "MT", "isin": "B", "name": "Beta", "decision": "WATCH", "score": 66.0},
        ]
    )
    action_meta = {
        "A": {"yahoo_ticker": "AAA", "sector_yf": "Technology"},
        "B": {"yahoo_ticker": "BBB", "sector_yf": "Industrials"},
    }
    prices = {
        "AAA": _prices(_returns(20, benchmark, 1.1)),
        "BBB": _prices(_returns(21, benchmark, 0.9)),
    }

    beta_calls = []
    real_compute = engine.compute_beta_metrics

    def counted_compute(returns, bench):
        beta_calls.append(id(returns))
        return real_compute(returns, bench)

    return_calls = []
    real_to_returns = engine.to_returns

    def counted_returns(price_series):
        return_calls.append(id(price_series))
        return real_to_returns(price_series)

    monkeypatch.setattr(engine, "compute_beta_metrics", counted_compute)
    monkeypatch.setattr(engine, "to_returns", counted_returns)
    out, returns_by_isin = engine.apply_risk_overlay(
        decisions,
        action_meta,
        {},
        prices,
        {},
        benchmark,
    )

    assert len(returns_by_isin) == 2
    assert len(beta_calls) == 2
    assert len(return_calls) == 2
    assert out.loc[out["isin"].eq("A"), "risk_beta_252d"].nunique(dropna=False) == 1
    assert out.loc[out["isin"].eq("B"), "risk_beta_252d"].nunique(dropna=False) == 1
    assert out["score"].tolist() == decisions["score"].tolist()
    assert out["decision"].tolist() == decisions["decision"].tolist()
