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


def evaluate_shadow_promotion(e: OosEvidence) -> tuple[bool, tuple[str, ...]]:
    reasons = []
    if not e.pit_complete:
        reasons.append("PIT_INCOMPLETE")
    if not e.independent_holdout:
        reasons.append("NO_INDEPENDENT_HOLDOUT")
    if e.periods < 12:
        reasons.append("INSUFFICIENT_PERIODS")
    if e.information_ratio is None:
        reasons.append("IR_MISSING")
    if e.turnover is None:
        reasons.append("TURNOVER_MISSING")
    if e.max_drawdown_delta is None:
        reasons.append("DRAWDOWN_COMPARISON_MISSING")
    if not e.ci_passed:
        reasons.append("CI_NOT_PASSED")
    if not e.independent_review:
        reasons.append("INDEPENDENT_REVIEW_MISSING")
    return (not reasons, tuple(reasons))
