from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CacheKey:
    namespace: str
    instrument_id: str
    as_of_date: str
    source_hash: str

    def digest(self) -> str:
        raw = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


class DeterministicJsonCache:
    def __init__(self, root: str | Path, *, ttl: timedelta, quarantine_dir: str | Path | None = None):
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        self.root = Path(root)
        self.ttl = ttl
        self.quarantine_dir = Path(quarantine_dir) if quarantine_dir else self.root / "quarantine"

    def _path(self, key: CacheKey) -> Path:
        return self.root / key.namespace / f"{key.digest()}.json"

    def put(self, key: CacheKey, value: Any, *, written_at: datetime | None = None) -> Path:
        now = written_at or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("written_at must be timezone-aware")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"key": key.__dict__, "written_at": now.isoformat(), "value": value}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
        return path

    def _quarantine(self, path: Path, reason: str) -> None:
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        target = self.quarantine_dir / f"{path.stem}.{reason}.json"
        try:
            os.replace(path, target)
        except OSError:
            return

    def get(self, key: CacheKey, *, now: datetime | None = None) -> Any | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("key") != key.__dict__:
                return None
            written = datetime.fromisoformat(payload["written_at"])
            if written.tzinfo is None:
                return None
            value = payload["value"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            self._quarantine(path, "invalid_payload")
            return None
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if current < written or current - written > self.ttl:
            return None
        return value

    def bulk_put(self, entries: list[tuple[CacheKey, Any]], *, written_at: datetime | None = None) -> tuple[Path, ...]:
        return tuple(self.put(key, value, written_at=written_at) for key, value in entries)

    def bulk_get(self, keys: list[CacheKey], *, now: datetime | None = None) -> dict[CacheKey, Any]:
        found: dict[CacheKey, Any] = {}
        for key in keys:
            value = self.get(key, now=now)
            if value is not None:
                found[key] = value
        return found
