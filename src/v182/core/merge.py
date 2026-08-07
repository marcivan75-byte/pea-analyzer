from __future__ import annotations
from dataclasses import dataclass

RANK = {"A": 4, "B": 3, "C": 2, "D": 1}
VALIDATION_STATUSES = {
    "VALIDATED",
    "ISIN_MATCHED",
    "AUTO_MATCH",
    "AUTO_MATCH_ISSUER_PROXY",
}


@dataclass(frozen=True)
class MergeDecision:
    action: str
    reason: str


def decide(existing: dict | None, incoming: dict) -> MergeDecision:
    if incoming.get("validation_status") not in VALIDATION_STATUSES:
        return MergeDecision("QUARANTINE", "IDENTITY_NOT_VALIDATED")
    if existing is None:
        return MergeDecision("INSERT", "FIRST_OBSERVATION")
    incoming_value = incoming.get("value")
    if incoming_value in (None, "", "MISSING", "UNKNOWN", "NON_OBSERVE"):
        return MergeDecision("KEEP", "NO_REGRESSION_MISSING")
    old_rank = RANK.get(existing.get("evidence_level", "D"), 0)
    new_rank = RANK.get(incoming.get("evidence_level", "D"), 0)
    if new_rank > old_rank:
        return MergeDecision("REPLACE", "HIGHER_EVIDENCE")
    if new_rank < old_rank:
        return MergeDecision("KEEP", "LOWER_EVIDENCE")
    if incoming.get("as_of", "") > existing.get("as_of", ""):
        return MergeDecision("REPLACE", "FRESHER_EQUAL_EVIDENCE")
    if incoming_value != existing.get("value"):
        return MergeDecision("QUARANTINE", "CONFLICT_EQUAL_EVIDENCE")
    return MergeDecision("KEEP", "NO_CHANGE")