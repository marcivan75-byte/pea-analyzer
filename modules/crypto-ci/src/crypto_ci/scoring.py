from __future__ import annotations

from typing import Any

from .utils import clamp, safe_mean


def score_asset(features: dict[str, Any], horizon: str, governance: dict[str, Any]) -> dict[str, Any]:
    weights = governance["weights"][horizon]
    blocks = features["blocks"][horizon]
    available = {name: value for name, value in blocks.items() if value is not None}
    coverage = sum(weights[name] for name in available)
    score = sum(weights[name] * value for name, value in available.items()) / coverage if coverage else None
    confirmations = sum(value >= 60.0 for value in available.values())
    confidence = safe_mean([
        coverage * 100.0,
        features["data_quality"],
        features["source_agreement"],
        clamp(features["source_count"] / 5.0 * 100.0),
    ]) or 0.0
    threshold = governance["thresholds"][horizon]

    if features["universe_flags"]:
        state = "BLOCKED_UNIVERSE"
    elif features["hard_risk_flags"]:
        state = "BLOCKED_RISK"
    elif coverage < threshold["minimum_coverage"]:
        state = "WAIT_DATA"
    elif confidence < threshold["minimum_confidence"]:
        state = "WAIT_CONFIDENCE"
    elif features["soft_risk_flags"]:
        state = "WAIT_RISK"
    elif score is not None and score >= threshold["strong"] and confirmations >= threshold["minimum_confirmations"] + 1:
        state = "STRONG_REVIEW"
    elif score is not None and score >= threshold["ready"] and confirmations >= threshold["minimum_confirmations"]:
        state = "READY_FOR_REVIEW"
    elif score is not None and score >= 55:
        state = "WATCH"
    else:
        state = "NO_SIGNAL"

    ranked = sorted(available.items(), key=lambda item: item[1], reverse=True)
    return {
        "asset_id": features["asset_id"],
        "symbol": features["symbol"],
        "name": features["name"],
        "horizon": horizon,
        "score": None if score is None else round(score, 4),
        "coverage": round(coverage, 4),
        "confidence": round(confidence, 4),
        "confirmations": confirmations,
        "state": state,
        "top_supports": [name for name, _ in ranked[:3]],
        "weakest_blocks": [name for name, _ in ranked[-2:]],
        "missing_blocks": [name for name in weights if name not in available],
        "universe_flags": features["universe_flags"],
        "hard_risk_flags": features["hard_risk_flags"],
        "soft_risk_flags": features["soft_risk_flags"],
        "blocks": {name: (None if value is None else round(value, 4)) for name, value in blocks.items()},
        "metrics": features["metrics"],
    }


def score_universe(feature_map: dict[str, Any], governance: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [score_asset(features, horizon, governance) for features in feature_map.values() for horizon in ("TCT", "CT")]
    return sorted(rows, key=lambda row: (row["horizon"], -(row["score"] if row["score"] is not None else -1.0), row["asset_id"]))
