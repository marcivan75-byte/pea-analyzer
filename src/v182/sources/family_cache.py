from __future__ import annotations

from datetime import datetime, timezone
import math


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_hours(value: object, now: datetime) -> float:
    parsed = _parse_utc(value)
    return math.inf if parsed is None else max(0.0, (now.astimezone(timezone.utc) - parsed).total_seconds() / 3600.0)


def family_values(entry: dict, family: str, *, legacy_names_key: str | None = None) -> dict[str, object]:
    """Return one family's values, migrating lazily from the historical flat cache."""
    direct = entry.get(f"{family}_values")
    if isinstance(direct, dict):
        return dict(direct)
    names = entry.get(legacy_names_key or f"{family}_fields") or []
    flat = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
    return {str(name): flat[name] for name in names if str(name) in flat}


def merge_family_values(families: dict[str, dict[str, object]], precedence_low_to_high: tuple[str, ...]) -> dict[str, object]:
    """Build the public flat view with deterministic source-family precedence."""
    merged: dict[str, object] = {}
    for family in precedence_low_to_high:
        merged.update(families.get(family) or {})
    return merged


def field_owner(field: str, families: dict[str, dict[str, object]], precedence_high_to_low: tuple[str, ...]) -> str | None:
    for family in precedence_high_to_low:
        if field in (families.get(family) or {}):
            return family
    return None


def family_failure_active(entry: dict, family: str, now: datetime, retry_ttl_hours: float) -> bool:
    return age_hours(entry.get(f"{family}_last_failed_at_utc"), now) < max(0.0, float(retry_ttl_hours))


def mark_family_failure(entry: dict, family: str, now: datetime, reason: str) -> None:
    entry[f"{family}_last_failed_at_utc"] = now.astimezone(timezone.utc).isoformat()
    entry[f"{family}_failure_reason"] = str(reason)
    entry[f"{family}_failure_count"] = min(9999, int(entry.get(f"{family}_failure_count") or 0) + 1)


def clear_family_failure(entry: dict, family: str) -> bool:
    changed = False
    for key in (f"{family}_last_failed_at_utc", f"{family}_failure_reason", f"{family}_failure_count"):
        if key in entry:
            entry.pop(key, None)
            changed = True
    return changed


def store_family_values(entry: dict, family: str, fresh: dict[str, object]) -> None:
    clean = {str(key): value for key, value in fresh.items() if value is not None}
    entry[f"{family}_values"] = clean
    entry[f"{family}_fields"] = sorted(clean)
