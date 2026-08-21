from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np


def finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def clip_score(value: float) -> float:
    return float(np.clip(value, 0.0, 100.0))


def mean_available(values: Iterable[Any]) -> float | None:
    clean = [finite(value) for value in values]
    observed = [value for value in clean if value is not None]
    return float(np.mean(observed)) if observed else None


def weighted_score(
    components: dict[str, float | None],
    weights: dict[str, float],
) -> tuple[float | None, float]:
    numerator = 0.0
    observed_weight = 0.0
    total_weight = float(sum(weights.values()))
    for key, weight in weights.items():
        value = finite(components.get(key))
        if value is None:
            continue
        numeric_weight = float(weight)
        numerator += clip_score(value) * numeric_weight
        observed_weight += numeric_weight
    if observed_weight <= 0 or total_weight <= 0:
        return None, 0.0
    return (
        clip_score(numerator / observed_weight),
        float(np.clip(observed_weight / total_weight, 0.0, 1.0)),
    )


def truthy(value: Any) -> bool | None:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "oui"}:
        return True
    if text in {"false", "0", "no", "non"}:
        return False
    return None
