from __future__ import annotations

from difflib import SequenceMatcher
import re

IDENTITY_SOURCE_PRIORITY = {
    "SEC_EDGAR": 100,
    "NASDAQ": 100,
    "EURONEXT": 100,
    "FINNHUB": 90,
    "ALPHA_VANTAGE": 70,
}


def _norm_name(value: object) -> str:
    text = "" if value is None else str(value).upper()
    text = re.sub(r"\b(INCORPORATED|INC|CORPORATION|CORP|LIMITED|LTD|PLC|HOLDINGS?|GROUP|PCL)\b", " ", text)
    return re.sub(r"[^A-Z0-9]+", "", text)


def _sources(row: dict) -> list[str]:
    return [source for source in str(row.get("sources", "")).split("|") if source]


def _identity_priority(row: dict) -> int:
    return max((IDENTITY_SOURCE_PRIORITY.get(source, 0) for source in _sources(row)), default=0)


def _is_us_exchange(value: object) -> bool:
    exchange = "" if value is None else str(value).upper()
    return any(token in exchange for token in ("NASDAQ", "NYSE", "AMEX", "NYSEAMERICAN"))


def _group_key(row: dict) -> tuple[str, str] | None:
    symbol = str(row.get("symbol") or "").strip().upper()
    expected_date = str(row.get("expected_date") or "").strip()
    if not symbol or not expected_date or not _is_us_exchange(row.get("exchange")):
        return None
    return symbol, expected_date


def _name_conflict(names: list[str]) -> bool:
    normalized = [name for name in (_norm_name(value) for value in names) if name]
    if len(set(normalized)) <= 1:
        return False
    for left_index, left in enumerate(normalized):
        for right in normalized[left_index + 1 :]:
            if SequenceMatcher(None, left, right).ratio() < 0.72:
                return True
    return False


def resolve_symbol_date_conflicts(rows: list[dict], id_builder) -> list[dict]:
    """Collapse duplicate US symbol/date events and expose material name conflicts.

    Calendar providers occasionally return the same US deal under divergent
    issuer names. Treating those as separate IPOs is operationally unsafe. The
    event is collapsed using an identity-specific source hierarchy while a
    material name disagreement becomes an explicit blocking DD condition.
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    passthrough: list[dict] = []
    for row in rows:
        key = _group_key(row)
        if key is None:
            passthrough.append(dict(row))
        else:
            grouped.setdefault(key, []).append(dict(row))

    resolved: list[dict] = list(passthrough)
    for key, group in grouped.items():
        if len(group) == 1:
            resolved.append(group[0])
            continue
        group.sort(key=_identity_priority, reverse=True)
        chosen = dict(group[0])
        all_sources = sorted(
            {source for row in group for source in _sources(row)},
            key=lambda source: IDENTITY_SOURCE_PRIORITY.get(source, 0),
            reverse=True,
        )
        names = [str(row.get("name") or "").strip() for row in group if str(row.get("name") or "").strip()]
        conflict = _name_conflict(names)
        for row in group[1:]:
            for field, value in row.items():
                if field in {"name", "symbol", "exchange", "candidate_id", "identity_key", "sources", "source_count"}:
                    continue
                existing = chosen.get(field)
                existing_missing = existing is None or existing == ""
                incoming_present = value is not None and value != ""
                if existing_missing and incoming_present:
                    chosen[field] = value
        chosen["sources"] = "|".join(all_sources)
        chosen["source_count"] = len(all_sources)
        chosen["identity_name_conflict"] = conflict
        chosen["identity_conflict_names"] = "|".join(dict.fromkeys(names)) if conflict else ""
        chosen["identity_conflict_sources"] = "|".join(all_sources) if conflict else ""
        chosen["identity_resolution_key"] = f"US_SYMBOL_DATE:{key[0]}:{key[1]}"
        chosen["candidate_id"] = id_builder(chosen)
        resolved.append(chosen)
    return resolved
