from __future__ import annotations

from pathlib import Path
from typing import Any

from .features import BLOCKS
from .io import load_json


class ConfigValidationError(RuntimeError):
    pass


def load_configs(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = root / "config"
    governance = load_json(config / "CRYPTO_GOVERNANCE_V1.json")
    sources = load_json(config / "CRYPTO_SOURCE_REGISTRY_V1.json")
    universe = load_json(config / "CRYPTO_UNIVERSE_V1.json")
    criteria = load_json(config / "CRYPTO_CRITERIA_REGISTRY_V1.json")
    return governance, sources, universe, criteria


def validate_loaded_configs(
    governance: dict[str, Any], sources: dict[str, Any], universe: dict[str, Any], criteria: dict[str, Any]
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if governance.get("asset_class") != "CRYPTO":
        errors.append("ASSET_CLASS_NOT_CRYPTO")
    if governance.get("real_orders_enabled") is not False:
        errors.append("REAL_ORDERS_MUST_BE_FALSE")
    if governance.get("automatic_weight_promotion") is not False:
        errors.append("AUTO_PROMOTION_MUST_BE_FALSE")
    for horizon in ("TCT", "CT"):
        weights = governance.get("weights", {}).get(horizon, {})
        if set(weights) != set(BLOCKS):
            errors.append(f"{horizon}_BLOCK_SET_INVALID")
        if abs(sum(float(value) for value in weights.values()) - 1.0) > 1e-12:
            errors.append(f"{horizon}_WEIGHTS_NOT_ONE")
        if any(float(value) <= 0 for value in weights.values()):
            errors.append(f"{horizon}_WEIGHT_NONPOSITIVE")
        incremental = governance.get("incremental_criteria_weights", {}).get(horizon, {})
        for block in ("trend_momentum", "liquidity_market_quality", "risk_quality"):
            criterion_weights = incremental.get(block, {})
            if abs(sum(float(value) for value in criterion_weights.values()) - 1.0) > 1e-12:
                errors.append(f"{horizon}_{block.upper()}_INCREMENTAL_WEIGHTS_NOT_ONE")
            if any(float(value) <= 0 for value in criterion_weights.values()):
                errors.append(f"{horizon}_{block.upper()}_INCREMENTAL_WEIGHT_NONPOSITIVE")
    timing = governance.get("t1_t2", {})
    if timing.get("scope") != "CRYPTO_TCT_ONLY":
        errors.append("T1_T2_SCOPE_INVALID")
    if float(timing.get("score_influence", -1.0)) != 0.0:
        errors.append("T1_T2_SCORE_INFLUENCE_MUST_BE_ZERO")
    if timing.get("real_orders_enabled") is not False:
        errors.append("T1_T2_REAL_ORDERS_MUST_BE_FALSE")
    for stage in ("t1", "t2"):
        components = timing.get(stage, {}).get("components", {})
        if abs(sum(float(value) for value in components.values()) - 1.0) > 1e-12:
            errors.append(f"{stage.upper()}_WEIGHTS_NOT_ONE")
    source_ids = [row.get("id") for row in sources.get("sources", [])]
    if len(source_ids) != len(set(source_ids)):
        errors.append("DUPLICATE_SOURCE_ID")
    for row in sources.get("sources", []):
        for key in ("base_url", "derivatives_base_url"):
            if row.get(key) and not str(row[key]).startswith("https://"):
                errors.append(f"NON_HTTPS_SOURCE:{row.get('id')}:{key}")
    asset_ids = [row.get("id") for row in universe.get("assets", [])]
    symbols = [row.get("symbol") for row in universe.get("assets", [])]
    if len(asset_ids) != len(set(asset_ids)) or None in asset_ids:
        errors.append("UNIVERSE_ID_NOT_UNIQUE")
    if len(symbols) != len(set(symbols)) or None in symbols:
        errors.append("UNIVERSE_SYMBOL_NOT_UNIQUE")
    if universe.get("universe_mode") != "TOP_MARKET_CAP_DYNAMIC":
        errors.append("UNIVERSE_MODE_MUST_BE_TOP_MARKET_CAP_DYNAMIC")
    target_count = int(universe.get("target_count", 0))
    if target_count != 100:
        errors.append("UNIVERSE_TARGET_COUNT_MUST_BE_100")
    if universe.get("assets_role") != "IDENTITY_AND_SOURCE_OVERRIDES_NOT_FULL_UNIVERSE":
        errors.append("UNIVERSE_ASSETS_ROLE_INVALID")
    classifications = universe.get("classification_overrides", {})
    if any(value not in {"STABLECOIN", "WRAPPED", "LEVERAGED"} for value in classifications.values()):
        errors.append("UNIVERSE_CLASSIFICATION_OVERRIDE_INVALID")
    for row in universe.get("assets", []):
        contract = row.get("contract")
        if contract and ":" not in contract:
            errors.append(f"AMBIGUOUS_CONTRACT:{row.get('id')}")
    criterion_ids = [row.get("id") for row in criteria.get("criteria", [])]
    if len(criterion_ids) != len(set(criterion_ids)):
        errors.append("DUPLICATE_CRITERION_ID")
    for row in criteria.get("criteria", []):
        if row.get("block") not in BLOCKS:
            errors.append(f"UNKNOWN_CRITERION_BLOCK:{row.get('id')}")
        if not set(row.get("horizons", [])).issubset({"TCT", "CT"}):
            errors.append(f"UNKNOWN_CRITERION_HORIZON:{row.get('id')}")
    if len(criteria.get("criteria", [])) < 25:
        warnings.append("CRITERIA_REGISTRY_SMALL")
    payload = {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "asset_count": target_count,
        "target_asset_count": target_count,
        "seed_override_count": len(asset_ids),
        "source_count": len(source_ids),
        "criterion_count": len(criterion_ids),
    }
    if errors:
        raise ConfigValidationError(";".join(errors))
    return payload


def validate_configs(root: Path) -> dict[str, Any]:
    return validate_loaded_configs(*load_configs(root))
