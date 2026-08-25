from __future__ import annotations

import json
import random
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .cache import DiskTTLCache, RateLimiter


class SourceError(RuntimeError):
    pass


class JsonHttpClient:
    def __init__(self, cache: DiskTTLCache, timeout: float = 12.0, retries: int = 2):
        self.cache = cache
        self.timeout = timeout
        self.retries = retries
        self._limiters: dict[str, RateLimiter] = {}

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        namespace: str,
        ttl_seconds: int,
        requests_per_second: float,
    ) -> Any:
        clean_params = {key: value for key, value in (params or {}).items() if value is not None}
        cache_key = f"{url}?{urlencode(sorted(clean_params.items()), doseq=True)}"
        cached = self.cache.get(namespace, cache_key, ttl_seconds)
        if cached is not None:
            return cached

        query = urlencode(clean_params, doseq=True)
        target = f"{url}?{query}" if query else url
        host = urlsplit(url).netloc
        limiter = self._limiters.setdefault(host, RateLimiter(requests_per_second))
        safe_url = urlunsplit((urlsplit(url).scheme, host, urlsplit(url).path, "", ""))
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            limiter.wait()
            try:
                request = Request(target, headers={"Accept": "application/json", "User-Agent": "crypto-ci/1.0", **(headers or {})})
                with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - governed fixed endpoint registry
                    data = json.loads(response.read().decode("utf-8"))
                self.cache.put(namespace, cache_key, data)
                return data
            except HTTPError as exc:
                last_error = exc
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        cooldown = float(retry_after) if retry_after is not None else min(8.0, 1.0 * (2**attempt))
                    except ValueError:
                        cooldown = min(8.0, 1.0 * (2**attempt))
                    limiter.defer(cooldown)
                if exc.code in {401, 403} or exc.code < 500 and exc.code != 429:
                    break
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(min(2.0, 0.25 * (2**attempt)) + random.uniform(0.0, 0.1))
        kind = type(last_error).__name__ if last_error else "UNKNOWN"
        raise SourceError(f"SOURCE_REQUEST_FAILED:{host}:{safe_url}:{kind}")
