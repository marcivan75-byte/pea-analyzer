from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import math


ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path("config/WEEKLY_V4_GOVERNANCE.json")
AUDIT = Path("outputs/audit/WEEKLY_V4_GOVERNANCE_AUDIT.json")


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    actual: object
    expected: object
    severity: str = "FATAL"


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"OBJECT_REQUIRED:{path.as_posix()}")
    return value


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _add(checks: list[Check], name: str, actual: object, expected: object, *, passed: bool | None = None) -> None:
    checks.append(Check(name=name, passed=bool(actual == expected if passed is None else passed), actual=actual, expected=expected))


def _weight_checks(
    checks: list[Check],
    *,
    registry: dict,
    prefix: str,
    groups: list[str],
    tolerance: float,
) -> None:
    weights = registry.get("weights", {})
    directions = registry.get("directions", {})
    for group in groups:
        vector = weights.get(group)
        _add(checks, f"{prefix}.{group}.vector_present", isinstance(vector, dict), True)
        if not isinstance(vector, dict):
            continue
        numeric = {str(key): float(value) for key, value in vector.items()}
        total = sum(numeric.values())
        _add(checks, f"{prefix}.{group}.weights_finite", all(math.isfinite(value) for value in numeric.values()), True)
        _add(checks, f"{prefix}.{group}.weights_non_negative", all(value >= 0 for value in numeric.values()), True)
        _add(checks, f"{prefix}.{group}.weights_sum", round(total, 12), 1.0, passed=abs(total - 1.0) <= tolerance)
        declared = directions.get(group, {})
        _add(checks, f"{prefix}.{group}.direction_keys", sorted(declared), sorted(numeric))


def run(root: Path = ROOT, *, write: bool = True) -> dict:
    cfg_path = root / CONFIG
    cfg = _load_json(cfg_path)
    checks: list[Check] = []
    refs = cfg["referentials"]
    tolerance = float(refs["weight_sum_tolerance"])

    action_path = root / refs["actions_registry"]
    etf_path = root / refs["etf_registry"]
    source_contract_path = root / cfg["source_contract"]
    confidence_path = root / "config/CI_ENTRY_CONFIDENCE_V22_2.json"
    full_integrity_path = root / "config/FULL_REFERENTIAL_INTEGRITY.json"

    for name, path in (
        ("actions_registry", action_path),
        ("etf_registry", etf_path),
        ("source_contract", source_contract_path),
        ("confidence_registry", confidence_path),
        ("full_referential_integrity", full_integrity_path),
    ):
        _add(checks, f"file.{name}.exists", path.exists(), True)

    actions = _load_json(action_path)
    etfs = _load_json(etf_path)
    source_contract = _load_json(source_contract_path)
    confidence = _load_json(confidence_path)
    full_integrity = _load_json(full_integrity_path)

    _add(checks, "actions.criteria_count.registry", int(actions["criteria_count"]), int(refs["actions_criteria_count"]))
    _add(checks, "actions.criteria_count.integrity", int(full_integrity["actions"]["criteria_count"]), int(refs["actions_criteria_count"]))
    _add(checks, "etf.criteria_count.registry", int(etfs["criteria_count"]), int(refs["etf_criteria_count"]))
    _add(checks, "etf.criteria_count.integrity", int(full_integrity["etf"]["criteria_count"]), int(refs["etf_criteria_count"]))
    _weight_checks(
        checks,
        registry=actions,
        prefix="actions",
        groups=list(refs["actions_weight_groups"]),
        tolerance=tolerance,
    )
    _weight_checks(
        checks,
        registry=etfs,
        prefix="etf",
        groups=list(refs["etf_weight_groups"]),
        tolerance=tolerance,
    )

    mt_name = str(refs["etf_dynamic_mt_weight_group"])
    mt_vector = etfs.get(mt_name, {})
    _add(checks, "etf.dynamic_mt.vector_present", isinstance(mt_vector, dict), True)
    if isinstance(mt_vector, dict):
        mt_values = [float(value) for value in mt_vector.values()]
        _add(checks, "etf.dynamic_mt.criteria_count", len(mt_values), int(refs["etf_dynamic_mt_criteria_count"]))
        _add(
            checks,
            "etf.dynamic_mt.weights_sum",
            round(sum(mt_values), 12),
            1.0,
            passed=abs(sum(mt_values) - 1.0) <= tolerance,
        )
        _add(checks, "etf.dynamic_mt.weights_non_negative", all(value >= 0 for value in mt_values), True)

    confidence_weights = {str(key): float(value) for key, value in confidence["confidence_weights"].items()}
    _add(
        checks,
        "confidence.weights_sum",
        round(sum(confidence_weights.values()), 12),
        1.0,
        passed=abs(sum(confidence_weights.values()) - 1.0) <= tolerance,
    )
    _add(checks, "confidence.weights_non_negative", all(value >= 0 for value in confidence_weights.values()), True)

    source_prefixes = ("boursorama_", "tradingview_", "investing_")
    weighted_keys: list[str] = []
    for registry in (actions, etfs):
        for vector in registry.get("weights", {}).values():
            if isinstance(vector, dict):
                weighted_keys.extend(str(key).lower() for key in vector)
    if isinstance(mt_vector, dict):
        weighted_keys.extend(str(key).lower() for key in mt_vector)
    _add(
        checks,
        "weights.post_selection_source_fields_absent",
        sorted(key for key in weighted_keys if key.startswith(source_prefixes)),
        [],
    )

    selection = cfg["selection"]
    _add(checks, "selection.score_range", float(selection["minimum_selection_score"]), "0..100", passed=0 <= float(selection["minimum_selection_score"]) <= 100)
    _add(checks, "selection.confidence_range", float(selection["minimum_confidence_score"]), "0..100", passed=0 <= float(selection["minimum_confidence_score"]) <= 100)
    _add(checks, "selection.etf_consensus_not_required", selection["etf_boursorama_analyst_consensus_required"], False)
    _add(checks, "selection.etf_morningstar_range", float(selection["etf_minimum_morningstar_stars"]), "1..5", passed=1 <= float(selection["etf_minimum_morningstar_stars"]) <= 5)

    _add(checks, "source.investing_disabled", source_contract["investing"]["enabled"], False)
    _add(checks, "source.tradingview_replaces_investing", source_contract["tradingview"]["replaces_investing"], True)
    _add(checks, "source.tradingview_exact_identity", source_contract["tradingview"]["exact_symbol_identity_proof_required"], True)
    _add(checks, "source.tradingview_free_name_search_forbidden", source_contract["tradingview"]["free_name_search_forbidden"], True)
    _add(checks, "source.raw_html_not_persisted", source_contract["tradingview"]["raw_html_persisted"], False)
    _add(checks, "source.missing_not_negative", source_contract["missing_data"]["negative_signal_imputation_forbidden"], True)
    _add(checks, "release.real_orders_disabled", cfg["release"]["real_orders_enabled"], False)
    _add(checks, "release.source_cannot_create_candidate", cfg["release"]["source_can_create_candidate"], False)
    _add(checks, "audits.iteration_count", int(cfg["audits"]["required_iterations"]), 5)

    failed = [check for check in checks if not check.passed and check.severity == "FATAL"]
    payload = {
        "status": "PASS" if not failed else "FAIL",
        "version": cfg["version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "reference_commit": cfg["reference"]["commit"],
        "checks": [asdict(check) for check in checks],
        "check_count": len(checks),
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "fatal_failures": [check.name for check in failed],
        "input_sha256": {
            path.relative_to(root).as_posix(): _digest(path)
            for path in (cfg_path, action_path, etf_path, source_contract_path, confidence_path, full_integrity_path)
        },
    }
    if write:
        target = root / AUDIT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    payload = run(ROOT)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
