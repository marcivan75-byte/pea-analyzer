from __future__ import annotations
from collections import defaultdict
from typing import Iterable

EVIDENCE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}
STATUS_RANK = {"VALIDATED": 4, "ISIN_MATCHED": 3, "AUTO_MATCH": 2, "REVIEW": 1, "REJECTED": 0}


def deduplicate(events: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Deduplicate identical economic events. Highest evidence wins.
    Equal-evidence conflicting payloads are quarantined, never silently merged.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        event_id = event.get("event_id")
        if not event_id:
            raise ValueError("event_id required")
        groups[event_id].append(event)

    kept: list[dict] = []
    quarantine: list[dict] = []
    for event_id, candidates in groups.items():
        candidates = sorted(
            candidates,
            key=lambda x: (EVIDENCE_RANK.get(x.get("evidence_level", "D"), 0),
                           STATUS_RANK.get(x.get("validation_status", "REVIEW"), 0),
                           x.get("publication_date", "")),
            reverse=True,
        )
        best = candidates[0]
        best_rank = EVIDENCE_RANK.get(best.get("evidence_level", "D"), 0)
        for other in candidates[1:]:
            other_rank = EVIDENCE_RANK.get(other.get("evidence_level", "D"), 0)
            if other_rank == best_rank and _economic_payload(other) != _economic_payload(best):
                quarantine.append({**other, "reason": "SMART_MONEY_EQUAL_EVIDENCE_CONFLICT"})
        kept.append(best)
    return kept, quarantine


def _economic_payload(e: dict) -> tuple:
    keys = ("direction", "quantity", "price", "value_eur", "stake_before", "stake_after",
            "threshold_pct", "short_position_pct")
    return tuple(e.get(k) for k in keys)
