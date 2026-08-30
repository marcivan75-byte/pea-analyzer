from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import pandas as pd


PARIS = ZoneInfo("Europe/Paris")
UTC = ZoneInfo("UTC")
TIMESTAMP_CANDIDATES = (
    "observed_at",
    "timestamp",
    "captured_at",
    "publication_timestamp",
    "source_timestamp",
    "as_of",
    "generated_at_utc",
)
SUPPORTED_SUFFIXES = {".csv", ".json", ".jsonl", ".parquet"}


class PITDataUnavailable(RuntimeError):
    """Raised when the requested PIT slice cannot be proven from timestamped audit data."""


@dataclass(frozen=True)
class PITSlice:
    as_of: datetime
    cutoff_utc: datetime
    observations: pd.DataFrame
    source_files: tuple[str, ...]


def pit_cutoff(as_of: date | datetime) -> datetime:
    """Return the strict anti-look-ahead cutoff: T-1 at 22:00 Europe/Paris, in UTC."""
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            local_day = as_of.date()
        else:
            local_day = as_of.astimezone(PARIS).date()
    else:
        local_day = as_of
    cutoff_local = datetime.combine(local_day - timedelta(days=1), time(22, 0), tzinfo=PARIS)
    return cutoff_local.astimezone(UTC)


def _read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, sep=None, engine="python")
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".jsonl":
        return pd.read_json(path, lines=True)
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict):
            for key in ("rows", "observations", "data", "records"):
                if isinstance(payload.get(key), list):
                    return pd.DataFrame(payload[key])
            return pd.DataFrame([payload])
    raise PITDataUnavailable(f"Unsupported PIT source format: {path}")


def _timestamp_column(frame: pd.DataFrame) -> str | None:
    by_lower = {str(c).lower(): str(c) for c in frame.columns}
    for candidate in TIMESTAMP_CANDIDATES:
        if candidate.lower() in by_lower:
            return by_lower[candidate.lower()]
    return None


def _normalize_timestamp(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True)


def load_pit_file(path: Path, as_of: date | datetime) -> pd.DataFrame:
    """Load one timestamped audit file and keep only observations available by T-1 22:00.

    No forward fill, no estimation and no fallback to a current master are permitted.
    """
    if not path.is_file():
        raise PITDataUnavailable(f"PIT source missing: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise PITDataUnavailable(f"Unsupported PIT source: {path}")

    frame = _read_frame(path)
    if frame.empty:
        raise PITDataUnavailable(f"PIT source empty: {path}")

    ts_col = _timestamp_column(frame)
    if ts_col is None:
        raise PITDataUnavailable(f"No timestamp column in PIT source: {path}")

    cutoff = pd.Timestamp(pit_cutoff(as_of))
    timestamps = _normalize_timestamp(frame[ts_col])
    eligible = frame.loc[timestamps.notna() & (timestamps <= cutoff)].copy()
    if eligible.empty:
        raise PITDataUnavailable(f"No observation available before PIT cutoff in {path}")

    eligible["_pit_observed_at_utc"] = timestamps.loc[eligible.index]
    eligible["_pit_source_file"] = str(path)
    eligible["_pit_timestamp_column"] = ts_col
    return eligible


def load_pit_observations(
    root: Path | str,
    as_of: date | datetime,
    *,
    audit_relpath: str = "outputs/audit",
    file_globs: tuple[str, ...] = ("*.csv", "*.json", "*.jsonl", "*.parquet"),
) -> PITSlice:
    """Load a proven PIT slice exclusively from outputs/audit.

    Files without a usable timestamp are ignored, but the overall operation fails closed
    if no eligible timestamped observation remains. Current master files are never read.
    """
    root_path = Path(root).resolve()
    audit_root = (root_path / audit_relpath).resolve()
    if root_path not in audit_root.parents and audit_root != root_path:
        raise PITDataUnavailable("Audit path escapes repository root")
    if not audit_root.is_dir():
        raise PITDataUnavailable(f"Audit directory missing: {audit_root}")

    frames: list[pd.DataFrame] = []
    used: list[str] = []
    seen: set[Path] = set()
    for pattern in file_globs:
        for path in sorted(audit_root.glob(pattern)):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                frame = load_pit_file(resolved, as_of)
            except PITDataUnavailable:
                continue
            frames.append(frame)
            used.append(str(resolved.relative_to(root_path)))

    if not frames:
        raise PITDataUnavailable(
            f"BLOCK_DATA: no timestamped observation <= T-1 22:00 found in {audit_relpath}"
        )

    observations = pd.concat(frames, ignore_index=True, sort=False)
    cutoff = pit_cutoff(as_of)
    as_of_dt = (
        as_of
        if isinstance(as_of, datetime)
        else datetime.combine(as_of, time(0, 0), tzinfo=PARIS)
    )
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=PARIS)
    return PITSlice(
        as_of=as_of_dt,
        cutoff_utc=cutoff,
        observations=observations,
        source_files=tuple(used),
    )
