from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class MarketRegime:
    breadth200: float
    median_perf_1m: float
    median_perf_6m: float
    market_above_sma200: bool


@dataclass(frozen=True)
class Candidate:
    instrument_id: str
    score_raw: float
    score_rank_pct: float
    exposure_group: str

    @property
    def score_final(self) -> float:
        return final_score(self.score_raw, self.score_rank_pct)


@dataclass(frozen=True)
class PositionState:
    entry_price: float
    close_price: float
    holding_sessions: int


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str | None
    return_pct: float


SCORE_RAW_WEIGHT = 0.55
SCORE_RANK_WEIGHT = 0.45
SELECTION_THRESHOLD = 82.0
TOP_N = 2
MAX_SIMILAR_ACTIVE_EXPOSURES = 2
TARGET_RETURN = 0.04
HARD_STOP_RETURN = -0.18
MAX_HOLDING_SESSIONS = 168


def weighted_raw_score(
    criterion_scores: Mapping[str, float],
    backtested_weights: Mapping[str, float],
) -> float:
    """Aggregate the 0-100 PIT criterion scores with the backtested MT weights.

    This function deliberately does not invent feature normalisation. The caller
    must provide the already-normalised 0-100 criterion scores used by the MT
    engine. All configured backtested criteria are mandatory: precision is
    prioritised over coverage and missing criteria block scoring.
    """
    missing = set(backtested_weights) - set(criterion_scores)
    if missing:
        raise ValueError(f"missing required MT criteria: {sorted(missing)}")

    total_weight = sum(float(weight) for weight in backtested_weights.values())
    if total_weight <= 0:
        raise ValueError("backtested weight total must be positive")

    weighted = 0.0
    for name, weight in backtested_weights.items():
        score = float(criterion_scores[name])
        if not 0.0 <= score <= 100.0:
            raise ValueError(f"criterion score out of 0-100 range: {name}={score}")
        weighted += score * float(weight)
    return weighted / total_weight


def final_score(score_raw: float, score_rank_pct: float) -> float:
    """Blend absolute MT score with its cross-sectional percentile rank."""
    return SCORE_RAW_WEIGHT * score_raw + SCORE_RANK_WEIGHT * score_rank_pct


def momo_risk_on(regime: MarketRegime) -> bool:
    """Strict ETF MT regime gate derived from the 2026-08-11 backtest."""
    return (
        regime.breadth200 >= 0.50
        and regime.median_perf_1m >= -0.01
        and regime.median_perf_6m > 0.0
        and regime.market_above_sma200
    )


def select_candidates(
    candidates: Iterable[Candidate],
    regime: MarketRegime,
    active_exposure_groups: Sequence[str] = (),
) -> list[Candidate]:
    """Return at most two qualifying ETF MT candidates.

    Precision is prioritized over coverage: outside MOMO_RISK_ON the result is
    empty. Candidates must have final score >= 82 and are ordered by score.
    No exposure group may exceed two active positions after selection.
    """
    if not momo_risk_on(regime):
        return []

    exposure_counts: dict[str, int] = {}
    for group in active_exposure_groups:
        exposure_counts[group] = exposure_counts.get(group, 0) + 1

    ranked = sorted(
        (c for c in candidates if c.score_final >= SELECTION_THRESHOLD),
        key=lambda c: c.score_final,
        reverse=True,
    )

    selected: list[Candidate] = []
    for candidate in ranked:
        count = exposure_counts.get(candidate.exposure_group, 0)
        if count >= MAX_SIMILAR_ACTIVE_EXPOSURES:
            continue
        selected.append(candidate)
        exposure_counts[candidate.exposure_group] = count + 1
        if len(selected) >= TOP_N:
            break
    return selected


def exit_decision(position: PositionState) -> ExitDecision:
    """Apply close-only +4% target, -18% hard stop, or 168-session time exit."""
    if position.entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if position.holding_sessions < 0:
        raise ValueError("holding_sessions cannot be negative")

    ret = position.close_price / position.entry_price - 1.0
    if ret >= TARGET_RETURN:
        return ExitDecision(True, "TARGET_CLOSE", ret)
    if ret <= HARD_STOP_RETURN:
        return ExitDecision(True, "STOP_CLOSE", ret)
    if position.holding_sessions >= MAX_HOLDING_SESSIONS:
        return ExitDecision(True, "TIME_CLOSE", ret)
    return ExitDecision(False, None, ret)
