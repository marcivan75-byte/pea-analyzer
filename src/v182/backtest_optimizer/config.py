from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json

DEFAULT_FEATURES: dict[str, tuple[tuple[str, ...], float]] = {
    "v10": (("Score V10 /100", "score_v10_100", "v10_score"), 0.2500),
    "momentum": (("score_momentum_100",), 0.1575),
    "quality": (("score_quality_100",), 0.1250),
    "catalyst": (("score_catalyst_100", "score_consensus_100", "score_analyst_momentum_100"), 0.0950),
    "risk": (("score_risk_100",), 0.0925),
    "value": (("score_value_100",), 0.0875),
    "expectancy": (("score_expectancy_100",), 0.0750),
    "structure": (("score_structure_100",), 0.0625),
    "sector": (("score_sector_100",), 0.0300),
    "fiscal": (("score_fiscal_100",), 0.0250),
    "technical": (("score_technical_100",), 0.0000),
    "fear_greed": (("score_fear_greed_100", "fear_greed_signal_score_100"), 0.0000),
    "smart_money": (("score_smart_money_100", "score_whales_insiders_100"), 0.0000),
    "decision_overlay": (("score_decision_overlay_100", "decision_support_score_100"), 0.0000),
}

ID_CANDIDATES = (
    "canonical_isin", "ISIN", "isin", "ticker", "symbol", "Code", "Nom société", "name"
)
DATE_CANDIDATES = ("snapshot_date", "as_of", "run_date", "date", "timestamp")
PRICE_CANDIDATES = ("canonical_last_close", "last_close", "Cours €", "price", "close")


@dataclass(frozen=True)
class OptimizerConfig:
    horizon_days: int = 28
    horizon_tolerance_days: int = 12
    top_k: int = 25
    candidate_count: int = 3000
    random_seed: int = 20421
    min_snapshots: int = 12
    min_test_snapshots: int = 4
    train_fraction: float = 0.67
    min_instruments_per_snapshot: int = 30
    transaction_cost_bps: float = 12.0
    baseline_blend: float = 0.45
    max_single_weight: float = 0.35
    max_optional_weight: float = 0.12
    max_weight_drift_l1: float = 0.55
    min_oos_improvement: float = 0.0025
    max_drawdown_worsening: float = 0.03
    objective_return_weight: float = 1.0
    objective_drawdown_weight: float = 0.65
    objective_vol_weight: float = 0.20
    objective_turnover_weight: float = 0.08
    objective_hit_rate_weight: float = 0.10
    feature_overrides: dict[str, dict[str, object]] = field(default_factory=dict)

    @classmethod
    def from_json(cls, path: str | Path) -> "OptimizerConfig":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in payload.items() if k in allowed})
