from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CONFIG_PATH = Path("data/reference/V21.1_GOLD_V1_CONFIG.json")


@dataclass(frozen=True)
class HorizonScore:
    horizon: str
    score: float | None
    weight_coverage: float
    observed_weight_pct: float
    family_contributions: dict[str, float]


def load_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_config(config: Mapping[str, Any]) -> None:
    families = config["families"]
    criteria = [c for family in families.values() for c in family["criteria"]]
    if len(criteria) != int(config["criteria_count"]):
        raise ValueError(f"criteria_count mismatch: {len(criteria)}")
    ids = [c["id"] for c in criteria]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate criterion ids")

    for horizon in ("mt", "ct"):
        family_key = f"weight_{horizon}_pct"
        family_sum = sum(float(f[family_key]) for f in families.values())
        criterion_sum = sum(float(c[family_key]) for c in criteria)
        if abs(family_sum - 100.0) > 1e-9:
            raise ValueError(f"{horizon} family weights sum to {family_sum}")
        if abs(criterion_sum - 100.0) > 1e-9:
            raise ValueError(f"{horizon} criterion weights sum to {criterion_sum}")
        for family_id, family in families.items():
            subtotal = sum(float(c[family_key]) for c in family["criteria"])
            if abs(subtotal - float(family[family_key])) > 1e-9:
                raise ValueError(
                    f"{horizon} family {family_id} criterion subtotal {subtotal} "
                    f"!= family weight {family[family_key]}"
                )

    if config.get("t1_t2_policy") != "EXCLUDED_GOLD; RESERVED_ACTIONS_TCT_ONLY":
        raise ValueError("Gold T1/T2 exclusion contract missing")
    if config.get("status") != "SHADOW_RESEARCH_ONLY_PENDING_BACKTEST":
        raise ValueError("Gold V1 must remain shadow until backtest validation")


def _criterion_weights(
    config: Mapping[str, Any], horizon: str, regime: str | None
) -> dict[str, tuple[str, float]]:
    key = f"weight_{horizon}_pct"
    multipliers: Mapping[str, float] = {}
    if regime:
        multipliers = config.get("regimes", {}).get(regime, {})
    cap = float(config.get("regime_multiplier_cap_pct", 25.0)) / 100.0
    adjusted: dict[str, tuple[str, float]] = {}
    total = 0.0

    for family_id, family in config["families"].items():
        multiplier = float(multipliers.get(family_id, 1.0))
        multiplier = min(1.0 + cap, max(1.0 - cap, multiplier))
        for criterion in family["criteria"]:
            weight = float(criterion[key]) * multiplier
            adjusted[criterion["id"]] = (family_id, weight)
            total += weight

    if total <= 0:
        raise ValueError("non-positive adjusted total weight")

    return {
        criterion_id: (family_id, weight * 100.0 / total)
        for criterion_id, (family_id, weight) in adjusted.items()
    }


def score_horizon(
    config: Mapping[str, Any],
    criteria_scores: Mapping[str, Any],
    horizon: str,
    regime: str | None = None,
) -> HorizonScore:
    if horizon not in {"mt", "ct"}:
        raise ValueError("horizon must be 'mt' or 'ct'")

    weights = _criterion_weights(config, horizon, regime)
    observed_weight = 0.0
    weighted_score = 0.0
    family_weighted: dict[str, float] = {}
    family_observed_weight: dict[str, float] = {}

    for criterion_id, (family_id, weight) in weights.items():
        raw = criteria_scores.get(criterion_id)
        if isinstance(raw, Mapping):
            raw = raw.get("score")
        if raw is None:
            continue
        value = float(raw)
        if not 0.0 <= value <= 100.0:
            raise ValueError(f"{criterion_id} score outside [0,100]: {value}")
        observed_weight += weight
        weighted_score += weight * value
        family_weighted[family_id] = family_weighted.get(family_id, 0.0) + weight * value
        family_observed_weight[family_id] = family_observed_weight.get(family_id, 0.0) + weight

    if observed_weight <= 0:
        return HorizonScore(
            horizon=horizon,
            score=None,
            weight_coverage=0.0,
            observed_weight_pct=0.0,
            family_contributions={},
        )

    family_contributions = {
        family_id: round(family_weighted[family_id] / family_observed_weight[family_id], 4)
        for family_id in family_weighted
        if family_observed_weight[family_id] > 0
    }
    return HorizonScore(
        horizon=horizon,
        score=round(weighted_score / observed_weight, 4),
        weight_coverage=round(observed_weight / 100.0, 6),
        observed_weight_pct=round(observed_weight, 4),
        family_contributions=family_contributions,
    )


def qds_from_snapshot(snapshot: Mapping[str, Any], mt_coverage: float, ct_coverage: float) -> float:
    explicit = snapshot.get("qds")
    if explicit is not None:
        qds = float(explicit)
        if not 0 <= qds <= 100:
            raise ValueError("qds must be inside [0,100]")
        return round(qds, 2)
    return round(100.0 * min(mt_coverage, ct_coverage), 2)


def mt_decision_label(config: Mapping[str, Any], mt_score: float | None) -> str:
    if mt_score is None:
        return "NO_DECISION"
    for rule in config["decision_thresholds_mt"]:
        if mt_score >= float(rule["min"]):
            return str(rule["label"])
    return "NO_DECISION"


def evaluate_snapshot(
    snapshot: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    config = dict(config or load_config())
    validate_config(config)

    criteria_scores = snapshot.get("criteria", {})
    if not isinstance(criteria_scores, Mapping):
        raise ValueError("snapshot.criteria must be an object")

    regime = snapshot.get("regime")
    if regime is not None and regime not in config.get("regimes", {}):
        raise ValueError(f"unknown regime: {regime}")

    mt = score_horizon(config, criteria_scores, "mt", regime)
    ct = score_horizon(config, criteria_scores, "ct", regime)
    qds = qds_from_snapshot(snapshot, mt.weight_coverage, ct.weight_coverage)
    gate_cfg = config["qds_gate"]

    active_gates = sorted(set(str(g) for g in snapshot.get("active_gates", [])))
    unknown_gates = sorted(set(active_gates) - set(config.get("hard_gates", [])))
    if unknown_gates:
        raise ValueError(f"unknown gates: {unknown_gates}")

    if qds < float(gate_cfg["no_decision_below"]):
        decision = "NO_DECISION_DATA_QUALITY"
        confidence = "BLOCKED"
    else:
        decision = mt_decision_label(config, mt.score)
        confidence = (
            "NORMAL"
            if qds >= float(gate_cfg["normal_decision_min"])
            else "REDUCED"
        )

    if "ANTI_LOOK_AHEAD" in active_gates:
        decision = "NO_DECISION_ANTI_LOOK_AHEAD"
        confidence = "BLOCKED"

    entry_score = None
    if mt.score is not None and ct.score is not None:
        formula = config["entry_score_formula"]
        entry_score = round(
            float(formula["mt"]) * mt.score + float(formula["ct"]) * ct.score, 4
        )

    return {
        "module": "GOLD_V1",
        "version": config["version"],
        "status": config["status"],
        "execution": "RESEARCH_ONLY",
        "execution_allowed": False,
        "regime": regime or "UNCLASSIFIED",
        "gold_score_mt": mt.score,
        "gold_score_ct": ct.score,
        "entry_score": entry_score,
        "qds_or": qds,
        "confidence": confidence,
        "decision_mt": decision,
        "weight_coverage_mt": mt.weight_coverage,
        "weight_coverage_ct": ct.weight_coverage,
        "family_scores_mt": mt.family_contributions,
        "family_scores_ct": ct.family_contributions,
        "active_gates": active_gates,
        "t1_t2_used": False,
        "backtest_validation_required": True,
    }
