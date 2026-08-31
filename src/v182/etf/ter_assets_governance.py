from __future__ import annotations

from dataclasses import dataclass
import math


PROOF_WEIGHTS = {"A": 1.0, "B": 0.6}


@dataclass(frozen=True)
class ETFCostAssetGate:
    status: str
    ter_pct: float | None
    fund_total_assets_eur_m: float | None
    proof_tier: str | None
    proof_weight: float
    reason: str


def _finite(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def proof_weight(proof_tier: str | None) -> float:
    tier = str(proof_tier or "").strip().upper()
    return PROOF_WEIGHTS.get(tier, 0.0)


def assess_ter_assets_gate(
    ter_pct: float | int | None,
    fund_total_assets_eur_m: float | int | None,
    *,
    proof_tier: str | None,
) -> ETFCostAssetGate:
    """Governed ETF liquidity/cost gate.

    TER must already be expressed in percent and assets must be an explicitly
    observed EUR million value. This function never performs FX conversion and
    never derives missing values. Missing/invalid values fail closed.
    """
    ter = _finite(ter_pct)
    assets = _finite(fund_total_assets_eur_m)
    tier = str(proof_tier or "").strip().upper() or None
    weight = proof_weight(tier)

    if ter is None or assets is None:
        return ETFCostAssetGate(
            status="BLOCK_DATA",
            ter_pct=ter,
            fund_total_assets_eur_m=assets,
            proof_tier=tier,
            proof_weight=weight,
            reason="MISSING_OR_INVALID_TER_OR_EUR_ASSETS",
        )
    if ter <= 0 or ter >= 100 or assets < 0:
        return ETFCostAssetGate(
            status="BLOCK_DATA",
            ter_pct=ter,
            fund_total_assets_eur_m=assets,
            proof_tier=tier,
            proof_weight=weight,
            reason="OUT_OF_DOMAIN_TER_OR_ASSETS",
        )
    if weight <= 0:
        return ETFCostAssetGate(
            status="BLOCK_DATA",
            ter_pct=ter,
            fund_total_assets_eur_m=assets,
            proof_tier=tier,
            proof_weight=weight,
            reason="UNSUPPORTED_PROOF_TIER",
        )
    if ter > 0.60 and assets < 100.0:
        return ETFCostAssetGate(
            status="BLOCK_DATA",
            ter_pct=ter,
            fund_total_assets_eur_m=assets,
            proof_tier=tier,
            proof_weight=weight,
            reason="TER_GT_0_60_AND_ASSETS_LT_100M_EUR",
        )
    return ETFCostAssetGate(
        status="ELIGIBLE_DATA",
        ter_pct=ter,
        fund_total_assets_eur_m=assets,
        proof_tier=tier,
        proof_weight=weight,
        reason="PASS_GOVERNED_TER_ASSETS_GATE",
    )
