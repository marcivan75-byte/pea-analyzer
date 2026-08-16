from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.decision import ipo_radar_operational_v1_1 as operational
from v182.decision.ipo_identity_resolution_v1 import resolve_symbol_date_conflicts

ROOT = operational.ROOT
legacy = operational.legacy
_BASE_DEDUP = legacy.deduplicate_candidates
_BASE_CLASSIFY = operational.classify_candidate
_BASE_ALERTS = operational.build_alerts

STABILIZED_DECISION_RANK = {
    "PRIORITY_DD": 0,
    "DEEP_DD": 1,
    "WATCH": 2,
    "WATCH_EARLY_FILING": 3,
    "WATCH_IDENTITY_CONFLICT": 4,
    "WATCH_DATA_GAP": 5,
    "AVOID_OR_LOW_EDGE": 6,
    "AVOID_HIGH_RISK": 7,
    "AVOID_HARD_BLOCK": 8,
    "AVOID_WITHDRAWN": 9,
}


def _truthy(value: object) -> bool:
    if value is True:
        return True
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def deduplicate_candidates_stabilized(rows: list[dict], source_priority: dict[str, int]) -> list[dict]:
    base = _BASE_DEDUP(rows, source_priority)
    return resolve_symbol_date_conflicts(base, legacy._candidate_id)


def classify_candidate_stabilized(row: dict, config: dict) -> str:
    status = str(row.get("status", "")).lower()
    if status == "withdrawn":
        return "AVOID_WITHDRAWN"
    flags = {flag.strip() for flag in str(row.get("hard_flags", "")).split("|") if flag.strip()}
    if flags.intersection(config["hard_block_flags"]):
        return "AVOID_HARD_BLOCK"
    if _truthy(row.get("identity_name_conflict")):
        return "WATCH_IDENTITY_CONFLICT"
    return _BASE_CLASSIFY(row, config)


def build_alerts_stabilized(evaluated: list[dict], history: pd.DataFrame) -> list[dict]:
    alerts = _BASE_ALERTS(evaluated, history)
    previous = legacy._previous_map(history)
    existing = {(str(item.get("identity_key")), str(item.get("alert"))) for item in alerts}
    for row in evaluated:
        if not _truthy(row.get("identity_name_conflict")):
            continue
        key = str(row.get("identity_key") or "")
        prior = previous.get(key)
        prior_conflict = _truthy(prior.get("identity_name_conflict")) if prior else False
        marker = (key, "IDENTITY_CONFLICT")
        if prior_conflict or marker in existing:
            continue
        names = str(row.get("identity_conflict_names") or "").strip()
        sources = str(row.get("identity_conflict_sources") or row.get("sources") or "").strip()
        detail = "Issuer identity conflict across IPO calendars"
        if names:
            detail += f": {names}"
        if sources:
            detail += f" [{sources}]"
        alerts.append(
            {
                "identity_key": key,
                "candidate_id": row.get("candidate_id"),
                "name": row.get("name"),
                "decision": row.get("decision"),
                "severity": "HIGH",
                "alert": "IDENTITY_CONFLICT",
                "detail": detail,
            }
        )
    return alerts


def install_stabilization() -> None:
    operational.DECISION_RANK.clear()
    operational.DECISION_RANK.update(STABILIZED_DECISION_RANK)
    legacy.DECISION_RANK.clear()
    legacy.DECISION_RANK.update(STABILIZED_DECISION_RANK)
    legacy.deduplicate_candidates = deduplicate_candidates_stabilized
    operational.classify_candidate = classify_candidate_stabilized
    operational.build_alerts = build_alerts_stabilized


def run(root: Path = ROOT) -> dict:
    install_stabilization()
    summary = operational.run(root)
    summary["stabilization_layer"] = "IPO_RADAR_STABILIZED_V1.1"
    summary["identity_policy"] = "US_SAME_SYMBOL_DATE_RECONCILED_MATERIAL_NAME_CONFLICT_QUARANTINED"
    summary_path = root / "outputs" / "ipo_radar" / "IPO_SUMMARY.json"
    if summary_path.exists():
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
