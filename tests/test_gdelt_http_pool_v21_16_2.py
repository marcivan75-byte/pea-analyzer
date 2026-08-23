from __future__ import annotations

import threading

from v182.sources import gdelt_news


def test_gdelt_http_session_reused_within_worker(monkeypatch):
    created = []
    calls = []

    class FakeSession:
        def __init__(self):
            created.append(self)

        def get(self, url, **kwargs):
            calls.append((self, url, kwargs))
            return object()

    monkeypatch.setattr(gdelt_news.requests, "Session", FakeSession)
    monkeypatch.setattr(gdelt_news, "_GDELT_HTTP_LOCAL", threading.local())

    first = gdelt_news._http_get("https://example.invalid/1", timeout=3)
    second = gdelt_news._http_get("https://example.invalid/2", timeout=4)

    assert first is not None
    assert second is not None
    assert len(created) == 1
    assert len(calls) == 2
    assert calls[0][0] is created[0]
    assert calls[1][0] is created[0]
    assert calls[0][2]["timeout"] == 3
    assert calls[1][2]["timeout"] == 4


def test_gdelt_http_session_is_thread_local(monkeypatch):
    created = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    class FakeSession:
        def __init__(self):
            with lock:
                created.append(self)

        def get(self, url, **kwargs):
            barrier.wait(timeout=2)
            return object()

    monkeypatch.setattr(gdelt_news.requests, "Session", FakeSession)
    monkeypatch.setattr(gdelt_news, "_GDELT_HTTP_LOCAL", threading.local())

    errors = []

    def worker(suffix):
        try:
            gdelt_news._http_get(f"https://example.invalid/{suffix}", timeout=1)
        except Exception as exc:  # pragma: no cover - assertion reports the failure
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(idx,)) for idx in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert len(created) == 2