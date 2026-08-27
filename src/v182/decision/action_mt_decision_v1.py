from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class MarketRegime:
    breadth200: float
    median_return_1m_pct: float
    median_return_6m_pct: float
    market_above_sma200: bool


@dataclass(frozen=True)
class ActionCandidate:
    isin: str
    score_raw: float
    score_rank_pct: float
    sector: str
    decision: str
    score_coverage: float
    warnings: str = ""

    def final_score(self, cfg: dict) -> float:
        policy = cfg["portfolio_decision"]
        return (
            float(policy["score_raw_weight"]) * self.score_raw
            + float(policy["cross_section_rank_weight"]) * self.score_rank_pct
        )


@dataclass(frozen=True)
class PortfolioDecision:
    selected: tuple[ActionCandidate, ...]
    abstention_reason: str | None
    rejected_counts: dict[str, int]


def regime_allowed(regime: MarketRegime, cfg: dict) -> bool:
    policy = cfg["portfolio_decision"]
    return (
        0.0 <= regime.breadth200 <= 1.0
        and regime.breadth200 >= float(policy["minimum_breadth_200"])
        and regime.median_return_1m_pct >= float(policy["minimum_median_return_1m_pct"])
        and regime.median_return_6m_pct > float(policy["minimum_median_return_6m_pct"])
        and (regime.market_above_sma200 or not bool(policy["require_market_above_sma200"]))
    )


def select_action_mt_candidates(
    candidates: Iterable[ActionCandidate],
    regime: MarketRegime,
    cfg: dict,
    active_sectors: Sequence[str] = (),
) -> PortfolioDecision:
    """Cross-sectional ACTION MT committee with explicit abstention and sector caps."""
    if not regime_allowed(regime, cfg):
        return PortfolioDecision((), "MARKET_REGIME_BLOCK", {"market_regime": 1})

    policy = cfg["portfolio_decision"]
    threshold = float(policy["selection_threshold"])
    minimum_coverage = float(cfg["gates"]["minimum_score_coverage"])
    allowed_states = {"ENTRY_READY_SHADOW", "ENTRY_STRONG_SHADOW"}
    hard_warnings = {"LIQUIDITY_BLOCK", "DRAWDOWN_BLOCK", "HOT_SECTOR_OVERVALUATION"}
    rejected = {"state": 0, "coverage": 0, "score": 0, "warning": 0, "sector_cap": 0}
    eligible: list[ActionCandidate] = []
    for candidate in candidates:
        if candidate.decision not in allowed_states:
            rejected["state"] += 1
            continue
        if candidate.score_coverage < minimum_coverage:
            rejected["coverage"] += 1
            continue
        if any(warning in hard_warnings for warning in candidate.warnings.split("|") if warning):
            rejected["warning"] += 1
            continue
        if candidate.final_score(cfg) < threshold:
            rejected["score"] += 1
            continue
        eligible.append(candidate)

    sector_counts: dict[str, int] = {}
    for sector in active_sectors:
        key = str(sector).strip().upper() or "UNCLASSIFIED"
        sector_counts[key] = sector_counts.get(key, 0) + 1

    selected: list[ActionCandidate] = []
    ranked = sorted(eligible, key=lambda item: (item.final_score(cfg), item.score_raw, item.isin), reverse=True)
    for candidate in ranked:
        sector = candidate.sector.strip().upper() or "UNCLASSIFIED"
        if sector_counts.get(sector, 0) >= int(policy["maximum_active_sector_exposure"]):
            rejected["sector_cap"] += 1
            continue
        selected.append(candidate)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) >= int(policy["maximum_new_positions"]):
            break

    reason = None if selected else "NO_ELIGIBLE_CANDIDATE"
    return PortfolioDecision(tuple(selected), reason, rejected)


def validate_decision_contract(cfg: dict) -> list[str]:
    """CI contract: return every unsafe or incoherent decision setting."""
    issues: list[str] = []
    weights = cfg.get("score_weights", {})
    if abs(sum(float(value) for value in weights.values()) - 1.0) > 1e-9:
        issues.append("SCORE_WEIGHTS_MUST_SUM_TO_ONE")
    policy = cfg.get("portfolio_decision", {})
    blend = float(policy.get("score_raw_weight", 0.0)) + float(policy.get("cross_section_rank_weight", 0.0))
    if abs(blend - 1.0) > 1e-9:
        issues.append("PORTFOLIO_SCORE_BLEND_MUST_SUM_TO_ONE")
    if int(policy.get("maximum_new_positions", 0)) < 1:
        issues.append("MAXIMUM_NEW_POSITIONS_INVALID")
    if float(cfg.get("gates", {}).get("minimum_score_coverage", 0.0)) < 0.70:
        issues.append("MINIMUM_SCORE_COVERAGE_TOO_LOW")
    governance = cfg.get("governance", {})
    if governance.get("real_orders_enabled") is not False:
        issues.append("REAL_ORDERS_MUST_REMAIN_DISABLED")
    if governance.get("holdout_locked") is not True:
        issues.append("HOLDOUT_MUST_REMAIN_LOCKED")
    if governance.get("structural_snapshot_can_promote_signal") is not False:
        issues.append("SNAPSHOT_PROMOTION_MUST_REMAIN_DISABLED")
    if governance.get("fixed_take_profit_enabled") is not False:
        issues.append("FIXED_TAKE_PROFIT_MUST_REMAIN_DISABLED")
    data_policy = cfg.get("data_policy", {})
    if data_policy.get("intraday_forbidden") is not True:
        issues.append("INTRADAY_MUST_REMAIN_FORBIDDEN")
    if data_policy.get("t1_t2_forbidden") is not True:
        issues.append("T1_T2_MUST_REMAIN_FORBIDDEN")
    if data_policy.get("completed_daily_bars_only") is not True:
        issues.append("COMPLETED_DAILY_BARS_REQUIRED")
    runtime = cfg.get("runtime", {})
    if runtime.get("cache_download_fallback_enabled") is not False:
        issues.append("HIDDEN_CACHE_DOWNLOAD_FALLBACK_FORBIDDEN")
    if runtime.get("pit_ledger_idempotent") is not True:
        issues.append("PIT_LEDGER_MUST_BE_IDEMPOTENT")
    return issues

