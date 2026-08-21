from __future__ import annotations

import hashlib
import json
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
    def __init__(self, root: str | Path, *, ttl: timedelta):
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        self.root = Path(root)
        self.ttl = ttl

    def _path(self, key: CacheKey) -> Path:
        return self.root / key.namespace / f"{key.digest()}.json"

    def put(self, key: CacheKey, value: Any, *, written_at: datetime | None = None) -> Path:
        now = written_at or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("written_at must be timezone-aware")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"key": key.__dict__, "written_at": now.isoformat(), "value": value}
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return path

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
            return None
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if current < written or current - written > self.ttl:
            return None
        return value
