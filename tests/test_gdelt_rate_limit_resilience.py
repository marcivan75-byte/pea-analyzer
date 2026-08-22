from __future__ import annotations

from v182.sources import gdelt_news


class _NoopLimiter:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self) -> None:
        self.calls += 1


class _Response:
    def __init__(self, *, status: int = 200, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload or {"articles": []}

    def raise_for_status(self) -> None:
        if self.status_code == 429:
            import requests

            raise requests.HTTPError("429 Client Error: Too Many Requests")

    def json(self) -> dict:
        return self._payload


def test_gdelt_fetch_retries_429_and_recovers(monkeypatch):
    responses = [
        _Response(status=429),
        _Response(status=429),
        _Response(payload={"articles": [{"title": "Recovered"}]}),
    ]
    calls = []
    sleeps = []
    limiter = _NoopLimiter()

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return responses.pop(0)

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr(gdelt_news.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(gdelt_news, "_GDELT_GLOBAL_LIMITER", _NoopLimiter())

    articles, error = gdelt_news.fetch_articles("TEST", limiter=limiter, timeout=1)

    assert error is None
    assert articles == [{"title": "Recovered"}]
    assert len(calls) == 3
    assert limiter.calls == 3
    assert sleeps == list(gdelt_news.GDELT_RETRY_BACKOFF_SECONDS)


def test_gdelt_fetch_does_not_retry_non_transient_error(monkeypatch):
    import requests

    calls = []
    sleeps = []
    limiter = _NoopLimiter()

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        raise requests.exceptions.InvalidURL("invalid query url")

    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr(gdelt_news.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(gdelt_news, "_GDELT_GLOBAL_LIMITER", _NoopLimiter())

    articles, error = gdelt_news.fetch_articles("TEST", limiter=limiter, timeout=1)

    assert articles == []
    assert error is not None and error.startswith("InvalidURL:")
    assert len(calls) == 1
    assert sleeps == []


def test_gdelt_has_provider_safe_global_start_interval():
    assert gdelt_news.GDELT_MIN_START_INTERVAL_SECONDS >= 1.0
    assert len(gdelt_news.GDELT_RETRY_BACKOFF_SECONDS) >= 2
