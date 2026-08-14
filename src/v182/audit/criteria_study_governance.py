from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import re


@dataclass(frozen=True)
class GovernanceFinding:
    severity: str
    code: str
    asset_class: str
    horizon: str
    criterion: str
    message: str


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _active_weights(registry: dict, horizon: str) -> dict[str, float]:
    return {
        str(name): float(weight or 0.0)
        for name, weight in registry.get("weights", {}).get(horizon, {}).items()
        if float(weight or 0.0) > 0.0
    }


def _matches_forbidden(name: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, name) for pattern in patterns)


def audit_action_registry(registry: dict, study: dict, tolerance: float = 1e-6) -> list[GovernanceFinding]:
    findings: list[GovernanceFinding] = []
    forbidden_patterns = list(study.get("forbidden_weight_patterns", []))
    shadow = set(registry.get("shadow_overlays_zero_weight", []))
    families = dict(registry.get("criterion_families", {}))
    budgets = dict(registry.get("family_budgets", {}))
    forbidden_exact = set(study.get("actions_study_hardening", {}).get("challenger_must_not_separately_weight", []))
    overlay_only = set(study.get("actions_study_hardening", {}).get("challenger_overlay_only", []))

    for horizon in ("CT", "MT", "LT", "SHORT", "TOP_DOWN"):
        active = _active_weights(registry, horizon)
        total = sum(active.values())
        if active and abs(total - 1.0) > tolerance:
            findings.append(GovernanceFinding("HIGH", "WEIGHT_SUM_NOT_ONE", "ACTION", horizon, "*", f"Active weights sum to {total:.10f}, expected 1.0."))
        for name in active:
            if _matches_forbidden(name, forbidden_patterns):
                findings.append(GovernanceFinding("HIGH", "FORBIDDEN_DERIVED_OR_CONTROL_WEIGHT", "ACTION", horizon, name, "Criterion matches a study-forbidden derived/control weighting pattern."))
            if name in forbidden_exact:
                findings.append(GovernanceFinding("HIGH", "DERIVED_DOUBLE_COUNT", "ACTION", horizon, name, "Study requires this derived signal to be folded into its canonical criterion instead of receiving a separate weight."))
            if name in overlay_only or name in shadow:
                findings.append(GovernanceFinding("HIGH", "OVERLAY_IN_BASE_ALPHA", "ACTION", horizon, name, "Study requires this field to remain a bounded overlay/shadow input, not a base alpha weight."))
            if name not in families:
                findings.append(GovernanceFinding("HIGH", "MISSING_FAMILY", "ACTION", horizon, name, "Every weighted criterion must belong to one explicit family before intra-family optimisation."))

        declared = budgets.get(horizon, {})
        if active and not declared:
            findings.append(GovernanceFinding("HIGH", "MISSING_FAMILY_BUDGET", "ACTION", horizon, "*", "Study requires explicit family budgets before intra-family optimisation."))
            continue
        actual: dict[str, float] = {}
        for name, weight in active.items():
            family = families.get(name)
            if family:
                actual[family] = actual.get(family, 0.0) + weight
        if declared:
            declared_sum = sum(float(v) for v in declared.values())
            if abs(declared_sum - 1.0) > tolerance:
                findings.append(GovernanceFinding("HIGH", "FAMILY_BUDGET_SUM_NOT_ONE", "ACTION", horizon, "*", f"Declared family budgets sum to {declared_sum:.10f}, expected 1.0."))
            for family in sorted(set(actual) | set(declared)):
                av = float(actual.get(family, 0.0))
                dv = float(declared.get(family, 0.0))
                if abs(av - dv) > tolerance:
                    findings.append(GovernanceFinding("HIGH", "FAMILY_BUDGET_MISMATCH", "ACTION", horizon, family, f"Actual weighted share {av:.10f} differs from declared family budget {dv:.10f}."))
    return findings


def audit_etf_registry(registry: dict, study: dict, tolerance: float = 1e-6) -> list[GovernanceFinding]:
    findings: list[GovernanceFinding] = []
    forbidden_patterns = list(study.get("forbidden_weight_patterns", []))
    for horizon, weights in registry.get("weights", {}).items():
        if not isinstance(weights, dict):
            continue
        active = {str(k): float(v or 0.0) for k, v in weights.items() if float(v or 0.0) > 0.0}
        if active and abs(sum(active.values()) - 1.0) > tolerance:
            findings.append(GovernanceFinding("HIGH", "WEIGHT_SUM_NOT_ONE", "ETF", horizon, "*", f"Active weights sum to {sum(active.values()):.10f}, expected 1.0."))
        for name in active:
            if _matches_forbidden(name, forbidden_patterns):
                findings.append(GovernanceFinding("HIGH", "FORBIDDEN_DERIVED_OR_CONTROL_WEIGHT", "ETF", horizon, name, "Criterion matches a study-forbidden derived/control weighting pattern."))
            if name.lower().startswith("t1") or name.lower().startswith("t2"):
                findings.append(GovernanceFinding("HIGH", "T1_T2_FORBIDDEN_ETF", "ETF", horizon, name, "T1/T2 are strictly Action TCT only."))
    return findings


def audit_mt_high_precision(mt: dict, study: dict, tolerance: float = 1e-9) -> list[GovernanceFinding]:
    findings: list[GovernanceFinding] = []
    etf = study.get("etf_study_hardening", {})
    dynamic = mt.get("dynamic_criteria", {})
    structural = mt.get("structural_overlay", {})
    if len(dynamic) != int(etf.get("mt_dynamic_pit_backtested_subblock_count", 38)):
        findings.append(GovernanceFinding("HIGH", "MT_DYNAMIC_COUNT_MISMATCH", "ETF", "MT", "*", f"Expected 38 dynamic PIT criteria, found {len(dynamic)}."))
    if len(structural) != int(etf.get("mt_structural_target_count", 5)):
        findings.append(GovernanceFinding("HIGH", "MT_STRUCTURAL_COUNT_MISMATCH", "ETF", "MT", "*", f"Expected 5 structural target criteria, found {len(structural)}."))
    if len(dynamic) + len(structural) != int(etf.get("mt_target_composite_criteria_count", 43)):
        findings.append(GovernanceFinding("HIGH", "MT_COMPOSITE_COUNT_MISMATCH", "ETF", "MT", "*", "Target composite must be 43 = 38 dynamic PIT + 5 structural."))
    split = etf.get("mt_target_split", {})
    if abs(float(split.get("dynamic", 0.0)) + float(split.get("structural", 0.0)) - 1.0) > tolerance:
        findings.append(GovernanceFinding("HIGH", "MT_TARGET_SPLIT_SUM", "ETF", "MT", "*", "69/31 target split must sum to 1.0."))
    status = str(mt.get("score", {}).get("recommended_69_31_composite_status", ""))
    if status != str(etf.get("mt_target_composite_status", "")):
        findings.append(GovernanceFinding("HIGH", "MT_COMPOSITE_STATUS_MISMATCH", "ETF", "MT", "*", "43-criterion composite must remain research-only until a dedicated PIT/OOS backtest."))
    return findings


def run(root: str | Path = ".", output: str | Path | None = None) -> dict:
    root = Path(root)
    study = load_json(root / "config" / "V21_6_3_CRITERIA_STUDY_GOVERNANCE.json")
    actions = load_json(root / "config" / "V21_ACTIONS_CRITERIA_REGISTRY.json")
    etf = load_json(root / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json")
    mt = load_json(root / "config" / "V20.8_ETF_MT_HIGH_PRECISION.json")
    findings = [
        *audit_action_registry(actions, study),
        *audit_etf_registry(etf, study),
        *audit_mt_high_precision(mt, study),
    ]
    payload = {
        "status": "PASS" if not any(f.severity == "HIGH" for f in findings) else "FAIL",
        "study_version": study.get("version"),
        "actions_registry_version": actions.get("version"),
        "etf_registry_version": etf.get("version"),
        "mt_version": mt.get("version"),
        "high": sum(f.severity == "HIGH" for f in findings),
        "findings": [asdict(f) for f in findings],
    }
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    payload = run(".", "outputs/audit/CRITERIA_STUDY_GOVERNANCE.json")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["status"] != "PASS":
        raise SystemExit(1)
