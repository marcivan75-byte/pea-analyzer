from pathlib import Path

import pandas as pd

from v182.backtest.hypothesis_backtest_v1 import run


def test_hypothesis_backtest_skips_without_files(tmp_path: Path):
    payload = run(root=tmp_path)
    assert payload["promotion_ready"] is False
    assert payload["decision_influence"] == 0.0
    assert payload["scopes"]["ETF_MT"]["status"] == "NO_OBSERVATIONS"


def test_hypothesis_backtest_winsorized_metrics(tmp_path: Path):
    folder = tmp_path / "outputs/backtest"
    folder.mkdir(parents=True)
    rows = []
    for i, day in enumerate(pd.date_range("2024-01-05", periods=8, freq="21D")):
        for j in range(10):
            rows.append(
                {
                    "isin": f"IS{j}",
                    "as_of": day.date().isoformat(),
                    "ETF_MT_SCORE": 10 + j + i,
                    "forward_return_pct_60d": -2 + j * 0.5,
                }
            )
    pd.DataFrame(rows).to_csv(folder / "ETF_MT_PIT_OBSERVATIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    payload = run(root=tmp_path)
    assert payload["scopes"]["ETF_MT"]["status"] == "OK"
    assert payload["scopes"]["ETF_MT"]["dynamic_p70"]["n_snapshots"] >= 1
    assert payload["real_orders_enabled"] is False
