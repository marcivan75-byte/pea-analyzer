from __future__ import annotations

from datetime import date

from v182.sources import sec_ipo
import v182.sources.sec_ipo_v2 as sec_v2


class _Response:
    def __init__(self, status_code: int, text: str = "", payload: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def test_sources_package_routes_runtime_to_hosted_runner_failover_collector() -> None:
    assert sec_ipo.__name__.endswith("sec_ipo_v2")


def test_efts_discovery_is_preferred_when_available(monkeypatch) -> None:
    payload = {
        "hits": {
            "total": {"value": 2},
            "hits": [
                {
                    "_id": "0001234567-26-000001:alpha-s1.htm",
                    "_source": {
                        "form_type": "S-1",
                        "file_date": "2026-08-13",
                        "entity_name": "Private Robotics Inc",
                        "ciks": ["0001234567"],
                    },
                },
                {
                    "_id": "0009876543-26-000002:cloud-f1.htm",
                    "_source": {
                        "form_type": "F-1",
                        "file_date": "2026-08-14",
                        "entity_name": "Foreign Cloud PLC",
                        "ciks": ["0009876543"],
                    },
                },
            ],
        }
    }

    def fake_get(url: str, **kwargs):
        del kwargs
        assert "efts.sec.gov" in url
        return _Response(200, payload=payload)

    monkeypatch.setattr(sec_v2.requests, "get", fake_get)
    monkeypatch.setattr(sec_v2, "collect_listed_ciks", lambda user_agent, timeout: (set(), {"status": "SUCCESS", "count": 0}))

    rows, status = sec_v2.collect_recent_registrations(
        date(2026, 8, 1), date(2026, 8, 16), "PEA-Analyzer test@example.com"
    )
    assert status["status"] == "SUCCESS"
    assert status["count"] == 2
    assert {row["form"] for row in rows} == {"S-1", "F-1"}
    assert "route=EFTS" in status["detail"]


def test_daily_index_is_used_when_efts_is_unavailable(monkeypatch) -> None:
    form_text = """Form Type   Company Name                                                  CIK        Date Filed  File Name
----------  ------------------------------------------------------------ ----------  ----------  --------------------------------------------
S-1         Private Robotics Inc                                         1234567890  2026-08-13  edgar/data/1234567890/0001.txt
F-1         Foreign Cloud PLC                                            9876543210  2026-08-13  edgar/data/9876543210/0002.txt
"""

    monkeypatch.setattr(sec_v2, "_collect_efts", lambda start, end, user_agent, timeout: ([], {"status": "FAILED", "detail": "HTTP_403"}))

    def fake_get(url: str, **kwargs):
        del kwargs
        if "20260813" in url:
            return _Response(200, form_text)
        return _Response(404, "")

    monkeypatch.setattr(sec_v2.requests, "get", fake_get)
    monkeypatch.setattr(sec_v2.time, "sleep", lambda _: None)
    monkeypatch.setattr(sec_v2, "collect_listed_ciks", lambda user_agent, timeout: (set(), {"status": "SUCCESS", "count": 0}))

    rows, status = sec_v2.collect_recent_registrations(
        date(2026, 8, 13), date(2026, 8, 14), "PEA-Analyzer test@example.com"
    )
    assert status["status"] == "SUCCESS"
    assert status["count"] == 2
    assert "route=DAILY_INDEX" in status["detail"]
    assert "daily_success=1" in status["detail"]
    assert "daily_missing=1" in status["detail"]


def test_discovery_survives_temporary_listed_filter_failure(monkeypatch) -> None:
    rows = [
        {"form": "S-1", "company": "Private Robotics Inc", "cik": "1234567890", "filed": "2026-08-13", "filename": "alpha.htm"}
    ]
    monkeypatch.setattr(sec_v2, "_collect_efts", lambda start, end, user_agent, timeout: (rows, {"status": "SUCCESS", "detail": "hits=1"}))
    monkeypatch.setattr(sec_v2, "collect_listed_ciks", lambda user_agent, timeout: (set(), {"status": "FAILED", "count": 0}))

    output, status = sec_v2.collect_recent_registrations(
        date(2026, 8, 13), date(2026, 8, 13), "PEA-Analyzer test@example.com"
    )
    assert len(output) == 1
    assert status["status"] == "PARTIAL"
    assert "listed_filter=FAILED" in status["detail"]
