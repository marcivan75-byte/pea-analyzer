from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting import weekly_unified_super_runner_v22_1 as runner
from v182.risk import beta_correlation_engine as risk_engine


def test_v22_1_reuses_exact_returns_and_beta_metrics(monkeypatch, tmp_path: Path) -> None:
    calls = {"returns": 0, "beta": 0}

    def fake_returns(prices):
        calls["returns"] += 1
        return prices.pct_change().dropna()

    def fake_beta(returns, benchmark):
        calls["beta"] += 1
        return {"beta_252d": 1.25, "status": "OK"}

    monkeypatch.setattr(risk_engine, "to_returns", fake_returns)
    monkeypatch.setattr(risk_engine, "compute_beta_metrics", fake_beta)

    def fake_previous_run(root):
        prices = pd.Series([100.0, 101.0, 102.0])
        benchmark = pd.Series([0.01, 0.02])
        r1 = risk_engine.to_returns(prices)
        r2 = risk_engine.to_returns(prices)
        assert r1 is r2
        b1 = risk_engine.compute_beta_metrics(r1, benchmark)
        b2 = risk_engine.compute_beta_metrics(r1, benchmark)
        assert b1 == b2
        return {"status": "SUCCESS"}

    monkeypatch.setattr(runner.previous, "run", fake_previous_run)
    payload = runner.run(tmp_path)

    assert payload["status"] == "SUCCESS"
    assert calls == {"returns": 1, "beta": 1}
    audit = (tmp_path / "outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V22_1.json").read_text(encoding="utf-8")
    assert '"risk_returns_cache_hits": 1' in audit
    assert '"risk_returns_cache_misses": 1' in audit
    assert '"risk_beta_cache_hits": 1' in audit
    assert '"risk_beta_cache_misses": 1' in audit
    assert '"risk_formulas_changed": false' in audit


def test_v22_1_beta_cache_is_benchmark_specific(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_beta(returns, benchmark):
        calls.append(id(benchmark))
        return {"beta_252d": float(len(calls)), "status": "OK"}

    monkeypatch.setattr(risk_engine, "compute_beta_metrics", fake_beta)

    def fake_previous_run(root):
        returns = pd.Series([0.01, 0.02])
        b1 = pd.Series([0.01, 0.01])
        b2 = pd.Series([0.02, 0.02])
        first = risk_engine.compute_beta_metrics(returns, b1)
        second = risk_engine.compute_beta_metrics(returns, b2)
        assert first["beta_252d"] != second["beta_252d"]
        return {"status": "SUCCESS"}

    monkeypatch.setattr(runner.previous, "run", fake_previous_run)
    runner.run(tmp_path)
    assert len(calls) == 2
