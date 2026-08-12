from __future__ import annotations

import threading
import time


class StartRateLimiter:
    """Thread-safe minimum interval between request starts.

    Workers may overlap network latency, while calls still start no faster than
    the configured interval. This keeps the previous source cadence ceiling
    without serialising the HTTP response time itself.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_allowed - now
            if sleep_for > 0:
                time.sleep(sleep_for)
                now = time.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self.min_interval_seconds
