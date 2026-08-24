from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json

import pandas as pd


MARKER_VERSION = "ETF_FUND_FLOW_SAME_DAY_REUSE_V1"


def _utc_day(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).date().isoformat()


def frame_fingerprint(frame: pd.DataFrame) -> str:
    """Stable content fingerprint used only to invalidate same-day reuse."""
    if frame is None or frame.empty:
        return sha256(b"EMPTY").hexdigest()
    normalized = frame.copy()
    normalized.columns = [str(column) for column in normalized.columns]
    columns = sorted(normalized.columns)
    normalized = normalized[columns].fillna("").astype(str)
    normalized = normalized.sort_values(columns, kind="mergesort").reset_index(drop=True)
    payload = normalized.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return sha256(payload).hexdigest()


def successful_snapshot_entries(snapshot: pd.DataFrame, failures: pd.DataFrame) -> list[dict[str, str]]:
    """Return only observations whose instrument had no collection failure at all."""
    if snapshot is None or snapshot.empty or "instrument_id" not in snapshot or "as_of" not in snapshot:
        return []
    failed_ids: set[str] = set()
    if failures is not None and not failures.empty and "instrument_id" in failures:
        failed_ids = set(failures["instrument_id"].dropna().astype(str))
    safe = snapshot[~snapshot["instrument_id"].astype(str).isin(failed_ids)].copy()
    if safe.empty:
        return []
    safe["as_of"] = pd.to_datetime(safe["as_of"], errors="coerce", utc=True)
    safe = safe[safe["as_of"].notna()].copy()
    safe["as_of"] = safe["as_of"].dt.date.astype(str)
    entries = safe[["instrument_id", "as_of"]].astype(str).drop_duplicates()
    entries = entries.sort_values(["instrument_id", "as_of"], kind="mergesort")
    return entries.to_dict(orient="records")


def merge_reuse_entries(previous: list[dict[str, str]], current: list[dict[str, str]]) -> list[dict[str, str]]:
    pairs = {
        (str(entry.get("instrument_id", "")), str(entry.get("as_of", "")))
        for entry in [*previous, *current]
        if str(entry.get("instrument_id", "")).strip() and str(entry.get("as_of", "")).strip()
    }
    return [
        {"instrument_id": instrument_id, "as_of": as_of}
        for instrument_id, as_of in sorted(pairs)
    ]


def load_same_day_reuse(
    marker_path: Path,
    history_path: Path,
    universe: pd.DataFrame,
    official: pd.DataFrame,
    *,
    enabled: bool,
    now: datetime | None = None,
) -> tuple[set[str], list[dict[str, str]], dict]:
    audit = {
        "reuse_enabled": bool(enabled),
        "reuse_day_utc": _utc_day(now),
        "reuse_marker": str(marker_path),
        "reuse_status": "DISABLED" if not enabled else "MISS",
        "reused_instruments": 0,
        "reused_snapshot_rows": 0,
        "universe_fingerprint": frame_fingerprint(universe),
        "official_input_fingerprint": frame_fingerprint(official),
    }
    if not enabled:
        return set(), [], audit
    if not marker_path.exists() or not history_path.exists():
        audit["reuse_status"] = "MISS_NO_MARKER_OR_HISTORY"
        return set(), [], audit
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception as exc:
        audit["reuse_status"] = "MISS_INVALID_MARKER"
        audit["reuse_detail"] = type(exc).__name__
        return set(), [], audit
    if marker.get("version") != MARKER_VERSION:
        audit["reuse_status"] = "MISS_MARKER_VERSION"
        return set(), [], audit
    if marker.get("collection_day_utc") != audit["reuse_day_utc"]:
        audit["reuse_status"] = "MISS_DIFFERENT_DAY"
        return set(), [], audit
    if marker.get("universe_fingerprint") != audit["universe_fingerprint"]:
        audit["reuse_status"] = "MISS_UNIVERSE_CHANGED"
        return set(), [], audit
    if marker.get("official_input_fingerprint") != audit["official_input_fingerprint"]:
        audit["reuse_status"] = "MISS_OFFICIAL_INPUT_CHANGED"
        return set(), [], audit

    raw_entries = marker.get("reusable_observations", [])
    entries = [
        {"instrument_id": str(entry.get("instrument_id", "")), "as_of": str(entry.get("as_of", ""))}
        for entry in raw_entries
        if isinstance(entry, dict) and entry.get("instrument_id") and entry.get("as_of")
    ]
    if not entries:
        audit["reuse_status"] = "MISS_NO_REUSABLE_SUCCESS"
        return set(), [], audit
    try:
        history = pd.read_csv(
            history_path,
            sep=";",
            encoding="utf-8-sig",
            usecols=lambda column: column in {"instrument_id", "as_of"},
            dtype=str,
            low_memory=False,
        )
    except Exception as exc:
        audit["reuse_status"] = "MISS_HISTORY_UNREADABLE"
        audit["reuse_detail"] = type(exc).__name__
        return set(), [], audit
    if history.empty or not {"instrument_id", "as_of"}.issubset(history.columns):
        audit["reuse_status"] = "MISS_HISTORY_KEYS"
        return set(), [], audit
    history_dates = pd.to_datetime(history["as_of"], errors="coerce", utc=True)
    valid = history_dates.notna()
    history_pairs = set(
        zip(
            history.loc[valid, "instrument_id"].astype(str),
            history_dates.loc[valid].dt.date.astype(str),
            strict=False,
        )
    )
    expected_by_id: dict[str, set[str]] = {}
    for entry in entries:
        expected_by_id.setdefault(entry["instrument_id"], set()).add(entry["as_of"])
    reusable_ids = {
        instrument_id
        for instrument_id, dates in expected_by_id.items()
        if all((instrument_id, as_of) in history_pairs for as_of in dates)
    }
    valid_entries = [entry for entry in entries if entry["instrument_id"] in reusable_ids]
    if not reusable_ids:
        audit["reuse_status"] = "MISS_HISTORY_MARKER_MISMATCH"
        return set(), [], audit
    audit["reuse_status"] = "HIT"
    audit["reused_instruments"] = int(len(reusable_ids))
    audit["reused_snapshot_rows"] = int(len(valid_entries))
    return reusable_ids, valid_entries, audit


def write_same_day_reuse_marker(
    marker_path: Path,
    universe: pd.DataFrame,
    official: pd.DataFrame,
    reusable_observations: list[dict[str, str]],
    *,
    now: datetime | None = None,
) -> dict:
    payload = {
        "version": MARKER_VERSION,
        "collection_day_utc": _utc_day(now),
        "generated_at_utc": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "universe_fingerprint": frame_fingerprint(universe),
        "official_input_fingerprint": frame_fingerprint(official),
        "reusable_observations": reusable_observations,
        "reusable_instruments": int(len({entry["instrument_id"] for entry in reusable_observations})),
        "failed_instruments_are_never_reused": True,
        "same_day_only": True,
        "decision_logic_changed": False,
    }
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = marker_path.with_suffix(marker_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(marker_path)
    return payload
