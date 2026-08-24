from __future__ import annotations

import numpy as np
import pandas as pd

from v182.reporting import wave3_cpu_budget_v21_15_4 as optimized
from v182.reporting import waves


def _history(tickers: list[str], periods: int = 280) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=periods, freq="B")
    columns = []
    data = {}
    for offset, ticker in enumerate(tickers):
        base = 100.0 + offset * 10.0 + np.linspace(0, 20, periods)
        values = {
            "Open": base * 0.999,
            "High": base * 1.01,
            "Low": base * 0.99,
            "Close": base,
            "Volume": np.full(periods, 100000.0 + offset * 1000.0),
            "Dividends": np.zeros(periods),
        }
        for field, series in values.items():
            key = (ticker, field)
            columns.append(key)
            data[key] = series
    return pd.DataFrame(data, index=index, columns=pd.MultiIndex.from_tuples(columns))


def _normalized_value(value):
    try:
        if bool(pd.isna(value)):
            return "<NA>"
    except (TypeError, ValueError):
        pass
    if isinstance(value, np.generic):
        return value.item()
    return value


def _canonical(rows: list[dict]) -> list[tuple]:
    return [
        (
            row.get("universe"),
            row.get("isin"),
            row.get("field"),
            _normalized_value(row.get("value")),
            row.get("source"),
            row.get("evidence_level"),
        )
        for row in rows
    ]


def test_parallel_action_wave3_is_value_and_order_equivalent_to_legacy() -> None:
    tickers = ["AAA.PA", "BBB.PA", "CCC.PA"]
    mapping = {ticker: f"ISIN{i}" for i, ticker in enumerate(tickers)}
    frames = [_history(tickers)]

    legacy = waves.wave3_derived_features(
        "unused",
        mapping,
        "ACTION",
        history_frames=frames,
    )
    parallel = optimized._action_derived_parallel(frames, mapping, workers=2)

    assert _canonical(parallel) == _canonical(legacy)


def test_wave3_cpu_contract_keeps_formulas_and_decisions_unchanged() -> None:
    audit = optimized.audit_contract()
    assert audit["action_compute_workers_max"] == 2
    assert audit["executor_map_order_preserved"] is True
    assert audit["feature_formula_changed"] is False
    assert audit["relative_strength_formula_changed"] is False
    assert audit["decision_logic_changed"] is False
    assert audit["weights_changed"] is False
    assert audit["thresholds_changed"] is False
