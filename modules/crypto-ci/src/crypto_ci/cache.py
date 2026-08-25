from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import threading
import time
from typing import Any


class DiskTTLCache:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        self._memory: dict[tuple[str, str], tuple[float, Any]] = {}

    def _path(self, namespace: str, key: str) -> Path:
        digest = sha256(key.encode("utf-8")).hexdigest()
        return self.root / namespace / f"{digest}.json"

    def get(self, namespace: str, key: str, ttl_seconds: int) -> Any | None:
        memory_key = (namespace, key)
        memory = self._memory.get(memory_key)
        if memory is not None and time.time() - memory[0] <= ttl_seconds:
            return memory[1]
        path = self._path(namespace, key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(payload["stored_at_epoch"]) <= ttl_seconds:
                self._memory[memory_key] = (float(payload["stored_at_epoch"]), payload["data"])
                return payload["data"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return None

    def put(self, namespace: str, key: str, data: Any) -> None:
        path = self._path(namespace, key)
        payload = {"stored_at_epoch": time.time(), "data": data}
        with self._lock:
            self._memory[(namespace, key)] = (float(payload["stored_at_epoch"]), data)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temporary.replace(path)


class RateLimiter:
    def __init__(self, requests_per_second: float):
        self.interval = 1.0 / max(requests_per_second, 0.01)
        self._next_start = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_start - now)
            self._next_start = max(now, self._next_start) + self.interval
        if delay:
            time.sleep(delay)

    def defer(self, seconds: float) -> None:
        """Share a provider cooldown (for example Retry-After) across all workers."""
        delay = max(0.0, min(float(seconds), 120.0))
        with self._lock:
            self._next_start = max(self._next_start, time.monotonic() + delay)
