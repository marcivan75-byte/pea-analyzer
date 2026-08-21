from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityInputs:
    freshness: float
    completeness: float
    pit_quality: float
    source_reliability: float
    sector_coverage: float


def composite_dqs(values: QualityInputs) -> float:
    parts = [
        values.freshness,
        values.completeness,
        values.pit_quality,
        values.source_reliability,
        values.sector_coverage,
    ]
    if any(v < 0 or v > 100 for v in parts):
        raise ValueError("quality inputs must be in [0, 100]")
    return round(
        0.25 * values.freshness
        + 0.25 * values.completeness
        + 0.20 * values.pit_quality
        + 0.15 * values.source_reliability
        + 0.15 * values.sector_coverage,
        4,
    )


def quality_state(score: float) -> str:
    if score < 65:
        return "QUARANTINE_CONTEXT_ONLY"
    if score < 80:
        return "SIGNAL_CONTEXT_ONLY"
    return "DECISION_QUALITY_ELIGIBLE_BUT_NO_LIVE_PROMOTION"
