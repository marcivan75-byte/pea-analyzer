from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityInputs:
    freshness: float
    completeness: float
    pit_quality: float
    source_reliability: float
    sector_coverage: float
    latency_sla_compliance: float = 100.0


def composite_dqs(values: QualityInputs, weights: dict[str, float] | None = None) -> float:
    weights = weights or {
        "freshness": 0.225,
        "completeness": 0.225,
        "pit_quality": 0.18,
        "source_reliability": 0.135,
        "sector_coverage": 0.135,
        "latency_sla_compliance": 0.10,
    }
    parts = [
        values.freshness,
        values.completeness,
        values.pit_quality,
        values.source_reliability,
        values.sector_coverage,
        values.latency_sla_compliance,
    ]
    if any(v < 0 or v > 100 for v in parts):
        raise ValueError("quality inputs must be in [0, 100]")
    required = set(values.__dict__)
    if (
        set(weights) != required
        or any(value < 0 for value in weights.values())
        or abs(sum(weights.values()) - 1.0) > 1e-9
    ):
        raise ValueError("quality weights must be non-negative, complete, and sum to 1")
    return round(sum(float(getattr(values, key)) * weight for key, weight in weights.items()), 4)


def quality_state(score: float) -> str:
    if score < 65:
        return "QUARANTINE_CONTEXT_ONLY"
    if score < 80:
        return "SIGNAL_CONTEXT_ONLY"
    return "DECISION_QUALITY_ELIGIBLE_BUT_NO_LIVE_PROMOTION"
