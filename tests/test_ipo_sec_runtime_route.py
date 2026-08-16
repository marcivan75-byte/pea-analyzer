from __future__ import annotations

from datetime import date

from v182.sources import sec_ipo
import v182.sources.sec_ipo_v2 as sec_v2


class _Response:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_sources_package_routes_runtime_to_daily_index_collector() -> None:
    assert sec_ipo.__name__.endswith("sec_ipo_v2")


def test_daily_index_discovery_treats_404_as_missing_day_not_source_failure(monkeypatch) -> None:
    form_text = """Form Type   Company Name                                                  CIK        Date Filed  File Name
----------  ------------------------------------------------------------ ----------  ----------  --------------------------------------------
S-1         Private Robotics Inc                                         1234567890  2026-08-13  edgar/data/1234567890/0001.txt
F-1         Foreign Cloud PLC                                            9876543210  2026-08-13  edgar/data/9876543210/0002.txt
"""

    def fake_get(url: str, headers: dict, timeout: int):
        del headers, timeout
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
    assert {row["form"] for row in rows} == {"S-1", "F-1"}
    assert "daily_success=1" in status["detail"]
    assert "daily_missing=1" in status["detail"]


def test_daily_index_can_operate_when_listed_filter_is_temporarily_unavailable(monkeypatch) -> None:
    form_text = """Form Type   Company Name                                                  CIK        Date Filed  File Name
----------  ------------------------------------------------------------ ----------  ----------  --------------------------------------------
S-1         Private Robotics Inc                                         1234567890  2026-08-13  edgar/data/1234567890/0001.txt
"""

    monkeypatch.setattr(sec_v2.requests, "get", lambda url, headers, timeout: _Response(200, form_text))
    monkeypatch.setattr(sec_v2.time, "sleep", lambda _: None)
    monkeypatch.setattr(sec_v2, "collect_listed_ciks", lambda user_agent, timeout: (set(), {"status": "FAILED", "count": 0}))

    rows, status = sec_v2.collect_recent_registrations(
        date(2026, 8, 13), date(2026, 8, 13), "PEA-Analyzer test@example.com"
    )
    assert len(rows) == 1
    assert status["status"] == "PARTIAL"
    assert "listed_filter=FAILED" in status["detail"]
