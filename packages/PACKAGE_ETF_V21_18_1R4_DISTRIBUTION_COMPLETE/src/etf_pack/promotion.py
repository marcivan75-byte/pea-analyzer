from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OosEvidence:
    pit_complete: bool
    independent_holdout: bool
    periods: int
    information_ratio: float | None
    turnover: float | None
    max_drawdown_delta: float | None
    ci_passed: bool
    independent_review: bool


@dataclass(frozen=True)
class PromotionThresholds:
    minimum_periods: int = 12
    minimum_information_ratio: float = 0.0
    maximum_turnover: float = 1.0
    minimum_drawdown_delta: float = 0.0


def evaluate_shadow_promotion(
    e: OosEvidence, thresholds: PromotionThresholds | None = None
) -> tuple[bool, tuple[str, ...]]:
    thresholds = thresholds or PromotionThresholds()
    reasons = []
    if not e.pit_complete:
        reasons.append("PIT_INCOMPLETE")
    if not e.independent_holdout:
        reasons.append("NO_INDEPENDENT_HOLDOUT")
    if e.periods < thresholds.minimum_periods:
        reasons.append("INSUFFICIENT_PERIODS")
    if e.information_ratio is None:
        reasons.append("IR_MISSING")
    if e.turnover is None:
        reasons.append("TURNOVER_MISSING")
    if e.max_drawdown_delta is None:
        reasons.append("DRAWDOWN_COMPARISON_MISSING")
    if e.information_ratio is not None and e.information_ratio < thresholds.minimum_information_ratio:
        reasons.append("IR_BELOW_THRESHOLD")
    if e.turnover is not None and e.turnover > thresholds.maximum_turnover:
        reasons.append("TURNOVER_ABOVE_THRESHOLD")
    if e.max_drawdown_delta is not None and e.max_drawdown_delta < thresholds.minimum_drawdown_delta:
        reasons.append("DRAWDOWN_DELTA_BELOW_THRESHOLD")
    if not e.ci_passed:
        reasons.append("CI_NOT_PASSED")
    if not e.independent_review:
        reasons.append("INDEPENDENT_REVIEW_MISSING")
    return (not reasons, tuple(reasons))
