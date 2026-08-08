from __future__ import annotations
from collections import defaultdict
from hashlib import sha256
from typing import Iterable

EVIDENCE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1}
STATUS_RANK = {"VALIDATED": 4, "ISIN_MATCHED": 3, "AUTO_MATCH": 2, "REVIEW": 1, "REJECTED": 0}


def economic_event_id(event: dict) -> str:
    """Source-independent identity used to prevent AMF/Finnhub double counting."""
    event_type = str(event.get("event_type") or "").upper()
    common = [
        str(event.get("isin") or "").upper(),
        event_type,
        str(event.get("actor_name") or "").strip().upper(),
        str(event.get("transaction_date") or event.get("position_date") or event.get("publication_date") or "")[:10],
        str(int(event.get("direction", 0) or 0)),
    ]
    if event_type == "INSIDER":
        specific = [_num(event.get("quantity")), _num(event.get("price"))]
    elif event_type == "THRESHOLD":
        specific = [_num(event.get("threshold_pct")), _num(event.get("stake_after"))]
    elif event_type == "SHORT":
        specific = [_num(event.get("short_position_pct"))]
    else:
        specific = [str(event.get("event_subtype") or "").upper(), _num(event.get("value_eur"))]
    return sha256("|".join(common + specific).encode("utf-8")).hexdigest()[:24]


def deduplicate(events: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Deduplicate economic events. Highest evidence wins.

    Source document IDs are deliberately excluded from the grouping key: the
    same transaction observed by AMF and Finnhub must contribute once. Equal-
    evidence conflicting payloads are quarantined, never silently merged.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        if not event.get("event_id"):
            raise ValueError("event_id required")
        groups[economic_event_id(event)].append(event)

    kept: list[dict] = []
    quarantine: list[dict] = []
    for economic_id, candidates in groups.items():
        candidates = sorted(
            candidates,
            key=lambda x: (
                EVIDENCE_RANK.get(x.get("evidence_level", "D"), 0),
                STATUS_RANK.get(x.get("validation_status", "REVIEW"), 0),
                x.get("publication_date", ""),
            ),
            reverse=True,
        )
        best = {**candidates[0], "economic_event_id": economic_id}
        best_rank = EVIDENCE_RANK.get(best.get("evidence_level", "D"), 0)
        for other in candidates[1:]:
            other_rank = EVIDENCE_RANK.get(other.get("evidence_level", "D"), 0)
            if other_rank == best_rank and _economic_payload(other) != _economic_payload(best):
                quarantine.append({**other, "economic_event_id": economic_id, "reason": "SMART_MONEY_EQUAL_EVIDENCE_CONFLICT"})
        kept.append(best)
    return kept, quarantine


def _economic_payload(e: dict) -> tuple:
    keys = (
        "direction", "quantity", "price", "value_eur", "stake_before", "stake_after",
        "threshold_pct", "short_position_pct",
    )
    return tuple(e.get(k) for k in keys)


def _num(value) -> str:
    try:
        return "" if value is None else f"{float(value):.10g}"
    except (TypeError, ValueError):
        return str(value or "")
