from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json

import pandas as pd


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    stale: int = 0
    invalid: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses + self.stale + self.invalid
        return self.hits / total if total else 0.0


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ActionMTHistoryCache:
    """Read-only governed OHLCV cache. Cache failures never trigger hidden downloads."""

    def __init__(self, root: Path, max_staleness_days: int = 7):
        self.root = root.resolve()
        self.max_staleness_days = int(max_staleness_days)
        self.stats = CacheStats()

    def _safe_path(self, isin: str) -> Path | None:
        key = "".join(char for char in str(isin).upper() if char.isalnum())
        if not key:
            return None
        for suffix in (".parquet", ".csv"):
            candidate = (self.root / f"{key}{suffix}").resolve()
            if self.root in candidate.parents and candidate.exists():
                return candidate
        return None

    def load(self, isin: str, as_of: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict]:
        path = self._safe_path(isin)
        if path is None:
            self.stats.misses += 1
            return pd.DataFrame(), {"status": "CACHE_MISS"}
        try:
            frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path, index_col=0, parse_dates=True)
            frame.index = pd.to_datetime(frame.index, errors="coerce")
            frame = frame[frame.index.notna()].sort_index()
            frame.columns = [str(column).lower() for column in frame.columns]
            if frame.empty or "close" not in frame:
                self.stats.invalid += 1
                return pd.DataFrame(), {"status": "CACHE_INVALID", "path": str(path)}
            latest = pd.Timestamp(frame.index.max()).normalize()
            reference = pd.Timestamp(as_of or pd.Timestamp.now(tz="UTC")).tz_localize(None).normalize()
            staleness = int((reference - latest.tz_localize(None)).days)
            metadata = {
                "status": "CACHE_HIT",
                "path": str(path),
                "sha256": file_sha256(path),
                "latest_session": latest.date().isoformat(),
                "staleness_days": staleness,
                "rows": int(len(frame)),
            }
            if staleness > self.max_staleness_days:
                self.stats.stale += 1
                metadata["status"] = "CACHE_STALE"
                return pd.DataFrame(), metadata
            self.stats.hits += 1
            return frame, metadata
        except Exception as exc:  # cache boundary: turn parser failures into governed diagnostics
            self.stats.invalid += 1
            return pd.DataFrame(), {"status": "CACHE_INVALID", "path": str(path), "error": type(exc).__name__}

    def manifest(self) -> dict:
        return {
            "cache_root": str(self.root),
            "max_staleness_days": self.max_staleness_days,
            "hits": self.stats.hits,
            "misses": self.stats.misses,
            "stale": self.stats.stale,
            "invalid": self.stats.invalid,
            "hit_rate": self.stats.hit_rate,
        }


def write_cache_manifest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

