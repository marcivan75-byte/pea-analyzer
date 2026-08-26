from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.decision import committee_master as committee_decision
from v182.reporting import weekly_unified_super_runner_v22_1 as runner


def test_v22_1_reuses_exact_same_series_percentile_rank(monkeypatch, tmp_path: Path) -> None:
    calls = {"pct": 0}

    def fake_pct(series, direction):
        calls["pct"] += 1
        return pd.Series([10.0, 50.0, 100.0], index=series.index)

    monkeypatch.setattr(committee_decision, "_pct_score", fake_pct)

    def fake_previous_run(root):
        series = pd.Series([1.0, 2.0, 3.0])
        first = committee_decision._pct_score(series, "HIGH")
        second = committee_decision._pct_score(series, "HIGH")
        pd.testing.assert_series_equal(first, second)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(runner.previous, "run", fake_previous_run)
    payload = runner.run(tmp_path)

    assert payload["status"] == "SUCCESS"
    assert calls["pct"] == 1
    audit = (tmp_path / "outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V22_1.json").read_text(encoding="utf-8")
    assert '"committee_percentile_cache_hits": 1' in audit
    assert '"committee_percentile_cache_misses": 1' in audit
    assert '"committee_percentile_semantics_changed": false' in audit


def test_v22_1_does_not_reuse_opposite_direction_rank(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_pct(series, direction):
        calls.append(direction)
        return pd.Series([1.0], index=series.index)

    monkeypatch.setattr(committee_decision, "_pct_score", fake_pct)

    def fake_previous_run(root):
        series = pd.Series([1.0])
        committee_decision._pct_score(series, "HIGH")
        committee_decision._pct_score(series, "LOW")
        return {"status": "SUCCESS"}

    monkeypatch.setattr(runner.previous, "run", fake_previous_run)
    runner.run(tmp_path)
    assert calls == ["HIGH", "LOW"]
