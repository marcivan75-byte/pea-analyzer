from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from statistics import fmean, median, pstdev
from typing import Any, Iterable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("NAIVE_DATETIME_FORBIDDEN")
    return dt.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def scale(value: float | None, bad: float, good: float) -> float | None:
    if value is None or good == bad:
        return None
    return clamp((value - bad) * 100.0 / (good - bad))


def safe_mean(values: Iterable[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return fmean(clean) if clean else None


def mean_if(values: Iterable[float | None], minimum_count: int) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return fmean(clean) if len(clean) >= minimum_count else None


def safe_median(values: Iterable[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return median(clean) if clean else None


def pct_change(values: list[float], periods: int) -> float | None:
    if periods <= 0 or len(values) <= periods or values[-periods - 1] == 0:
        return None
    return (values[-1] / values[-periods - 1] - 1.0) * 100.0


def sma(values: list[float], window: int) -> float | None:
    return fmean(values[-window:]) if window > 0 and len(values) >= window else None


def rsi(values: list[float], window: int = 14) -> float | None:
    if len(values) <= window:
        return None
    deltas = [values[i] - values[i - 1] for i in range(len(values) - window, len(values))]
    gains = fmean(max(delta, 0.0) for delta in deltas)
    losses = fmean(max(-delta, 0.0) for delta in deltas)
    if losses == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def annualized_volatility(values: list[float], window: int = 30) -> float | None:
    if len(values) <= window:
        return None
    returns = []
    for previous, current in zip(values[-window - 1 : -1], values[-window:]):
        if previous > 0 and current > 0:
            returns.append(math.log(current / previous))
    return pstdev(returns) * math.sqrt(365.0) * 100.0 if len(returns) >= 10 else None


def annualized_downside_volatility(values: list[float], window: int = 30) -> float | None:
    if len(values) <= window:
        return None
    returns = [
        min(0.0, math.log(current / previous))
        for previous, current in zip(values[-window - 1 : -1], values[-window:])
        if previous > 0 and current > 0
    ]
    return math.sqrt(sum(value * value for value in returns) / len(returns)) * math.sqrt(365.0) * 100.0 if len(returns) >= 10 else None


def weighted_mean(values: list[tuple[float | None, float]], minimum_count: int = 1) -> float | None:
    clean = [(float(value), float(weight)) for value, weight in values if value is not None and math.isfinite(float(value)) and weight > 0]
    total = sum(weight for _, weight in clean)
    return sum(value * weight for value, weight in clean) / total if len(clean) >= minimum_count and total > 0 else None


def max_drawdown(values: list[float], window: int = 90) -> float | None:
    sample = values[-window:]
    if not sample:
        return None
    peak = sample[0]
    worst = 0.0
    for value in sample:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value / peak - 1.0) * 100.0)
    return worst


def change_between_windows(values: list[float], window: int = 30) -> float | None:
    if len(values) < 2 * window:
        return None
    current = fmean(values[-window:])
    previous = fmean(values[-2 * window : -window])
    return (current / previous - 1.0) * 100.0 if previous else None


def zscore_last(values: list[float], window: int = 30) -> float | None:
    sample = values[-window:]
    if len(sample) < 10:
        return None
    sigma = pstdev(sample)
    return (sample[-1] - fmean(sample)) / sigma if sigma else 0.0


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(encoded).hexdigest()
