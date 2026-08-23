from __future__ import annotations

import pandas as pd
from pandas.testing import assert_frame_equal

from v182.decision.committee_master import active_criteria, resolve_field
from v182.decision.effective_weights import effective_weight_report


def _legacy_effective_weight_report(
    frame: pd.DataFrame,
    registry: dict,
    asset_class: str,
    horizons: list[str],
) -> pd.DataFrame:
    rows = []
    for horizon in horizons:
        active = active_criteria(registry, horizon)
        if not active:
            continue
        resolved = {}
        for name, weight, direction in active:
            values, source = resolve_field(frame, name)
            resolved[name] = (values, source, float(weight), direction)
        for idx, row in frame.iterrows():
            available = []
            for name, (values, source, weight, direction) in resolved.items():
                ok = values is not None and idx in values.index and pd.notna(values.loc[idx])
                if ok:
                    available.append((name, source, weight, direction))
            denom = sum(item[2] for item in available)
            for name, (values, source, weight, direction) in resolved.items():
                ok = values is not None and idx in values.index and pd.notna(values.loc[idx])
                effective = weight / denom * 100.0 if ok and denom > 0 else 0.0
                rows.append(
                    {
                        "asset_class": asset_class,
                        "horizon": horizon,
                        "isin": str(row.get("isin", "") or ""),
                        "name": str(row.get("name", "") or ""),
                        "criterion": name,
                        "raw_weight_pct": round(weight * 100.0, 6),
                        "criterion_available": bool(ok),
                        "effective_weight_pct": round(effective, 6),
                        "available_raw_weight_pct": round(denom * 100.0, 6),
                        "normalization_policy": "AVAILABLE_CRITERIA_RENORMALIZED_TO_100",
                        "resolution": source,
                    }
                )
    return pd.DataFrame(rows)


def test_vectorized_effective_weights_matches_legacy_output_exactly() -> None:
    frame = pd.DataFrame(
        {
            "isin": ["A", "B", "C", None],
            "name": ["Alpha", "Beta", None, "Delta"],
            "criterion_a": [10.0, None, 30.0, 40.0],
            "criterion_b": [1.0, 2.0, None, 4.0],
        },
        index=[11, 22, 33, 44],
    )
    registry = {
        "weights": {
            "CT": {"criterion_a": 0.50, "criterion_b": 0.30, "missing_field": 0.20},
            "MT": {"criterion_b": 0.75, "criterion_a": 0.25},
        },
        "directions": {
            "CT": {"criterion_a": "HIGH", "criterion_b": "LOW", "missing_field": "HIGH"},
            "MT": {"criterion_b": "HIGH", "criterion_a": "LOW"},
        },
    }
    horizons = ["CT", "MT"]

    legacy = _legacy_effective_weight_report(frame, registry, "ACTION", horizons)
    vectorized = effective_weight_report(frame, registry, "ACTION", horizons)

    assert_frame_equal(vectorized, legacy, check_dtype=False, check_exact=True)


def test_vectorized_effective_weights_preserves_horizon_row_criterion_order() -> None:
    frame = pd.DataFrame(
        {
            "isin": ["A", "B"],
            "name": ["Alpha", "Beta"],
            "criterion_a": [1.0, 2.0],
            "criterion_b": [3.0, 4.0],
        }
    )
    registry = {
        "weights": {
            "CT": {"criterion_a": 0.6, "criterion_b": 0.4},
            "MT": {"criterion_b": 1.0},
        },
        "directions": {
            "CT": {"criterion_a": "HIGH", "criterion_b": "HIGH"},
            "MT": {"criterion_b": "HIGH"},
        },
    }

    result = effective_weight_report(frame, registry, "ACTION", ["CT", "MT"])
    observed = list(zip(result["horizon"], result["isin"], result["criterion"]))
    assert observed == [
        ("CT", "A", "criterion_a"),
        ("CT", "A", "criterion_b"),
        ("CT", "B", "criterion_a"),
        ("CT", "B", "criterion_b"),
        ("MT", "A", "criterion_b"),
        ("MT", "B", "criterion_b"),
    ]
