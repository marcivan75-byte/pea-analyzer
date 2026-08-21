from __future__ import annotations

from datetime import date

import pytest

from v182.sources import euronext_ipo_v1_3 as source


def _page(*rows: tuple[str, str, str, str, str, str]) -> str:
    body = "".join(
        f"<tr><td>{name}</td><td>{listing_date}</td><td>{isin}</td>"
        f"<td>{location}</td><td>{market}</td><td>{ticker}</td></tr>"
        for name, listing_date, isin, location, market, ticker in rows
    )
    links = "".join(
        f'<a href="/en/ipo-showcase/{ticker.lower()}">{name}</a>'
        for name, _, _, _, _, ticker in rows
    )
    return (
        "<html><body>"
        "<table><thead><tr>"
        "<th>Company name</th><th>Date</th><th>ISIN code</th>"
        "<th>Location</th><th>Market</th><th>Ticker</th>"
        f"</tr></thead><tbody>{body}</tbody></table>{links}</body></html>"
    )


class _Response:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_historical_target_on_later_page_is_collected(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        source.EURONEXT_IPO_ALL: _page(
            ("Recent SA", "20/08/2026", "FR0000000001", "Paris", "Euronext", "REC")
        ),
        f"{source.EURONEXT_IPO_ALL}?page=1": _page(
            ("DOF Group", "22/06/2023", "NO0012851874", "Oslo", "Euronext", "DOFG")
        ),
        f"{source.EURONEXT_IPO_ALL}?page=2": _page(
            ("Old SA", "15/12/2022", "FR0000000002", "Paris", "Euronext", "OLD")
        ),
    }
    requested: list[str] = []

    def fake_get(url: str, **_: object) -> _Response:
        requested.append(url)
        return _Response(pages[url])

    monkeypatch.setattr(source.requests, "get", fake_get)
    monkeypatch.setattr(
        source,
        "_fetch_detail",
        lambda url, headers, timeout: {
            "euronext_showcase_url": url,
            "euronext_detail_status": "SUCCESS",
            "euronext_ipo_date_text": "",
        },
    )

    candidates, metrics = source.collect_euronext_v1_3(
        date(2023, 1, 1), date(2026, 8, 20), max_pages=10
    )

    assert {row["isin"] for row in candidates} == {"FR0000000001", "NO0012851874"}
    dof = next(row for row in candidates if row["isin"] == "NO0012851874")
    assert dof["expected_date"] == "2023-06-22"
    assert dof["euronext_source_page"].endswith("?page=1")
    assert metrics["status"] == "SUCCESS"
    assert metrics["pagination_complete"] is True
    assert metrics["pages_fetched"] == 3
    assert metrics["stop_reason"] == "PAGE_WHOLELY_BEFORE_REQUESTED_START"
    assert requested == [
        source.EURONEXT_IPO_ALL,
        f"{source.EURONEXT_IPO_ALL}?page=1",
        f"{source.EURONEXT_IPO_ALL}?page=2",
    ]


def test_duplicate_candidate_across_pages_is_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    duplicate = ("Lepermislibre", "13/02/2023", "FR001400F2Z1", "Paris", "Growth", "ALLPL")
    pages = {
        source.EURONEXT_IPO_ALL: _page(duplicate),
        f"{source.EURONEXT_IPO_ALL}?page=1": _page(
            duplicate,
            ("Older SA", "30/12/2022", "FR0000000003", "Paris", "Euronext", "OLDER"),
        ),
        f"{source.EURONEXT_IPO_ALL}?page=2": _page(
            ("Older Two SA", "15/12/2022", "FR0000000004", "Paris", "Euronext", "OLD2")
        ),
    }

    monkeypatch.setattr(source.requests, "get", lambda url, **_: _Response(pages[url]))
    monkeypatch.setattr(source, "_fetch_detail", lambda *args, **kwargs: {})

    candidates, metrics = source.collect_euronext_v1_3(
        date(2023, 1, 1), date(2026, 8, 20), max_pages=5
    )

    assert [row["isin"] for row in candidates] == ["FR001400F2Z1"]
    assert metrics["duplicate_candidates_removed"] == 1
    assert metrics["pagination_complete"] is True
    assert metrics["stop_reason"] == "PAGE_WHOLELY_BEFORE_REQUESTED_START"


def test_repeated_page_signature_stops_without_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    html = _page(("Recent SA", "20/08/2026", "FR0000000001", "Paris", "Euronext", "REC"))
    requested: list[str] = []

    def fake_get(url: str, **_: object) -> _Response:
        requested.append(url)
        return _Response(html)

    monkeypatch.setattr(source.requests, "get", fake_get)
    monkeypatch.setattr(source, "_fetch_detail", lambda *args, **kwargs: {})

    candidates, metrics = source.collect_euronext_v1_3(
        date(2023, 1, 1), date(2026, 8, 20), max_pages=20
    )

    assert len(candidates) == 1
    assert metrics["status"] == "SUCCESS"
    assert metrics["stop_reason"] == "REPEATED_PAGE_SIGNATURE"
    assert metrics["pages_fetched"] == 2
    assert len(requested) == 2


def test_later_page_fetch_failure_marks_collection_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _page(("Recent SA", "20/08/2026", "FR0000000001", "Paris", "Euronext", "REC"))

    def fake_get(url: str, **_: object) -> _Response:
        if url == source.EURONEXT_IPO_ALL:
            return _Response(first)
        return _Response("", status_code=503)

    monkeypatch.setattr(source.requests, "get", fake_get)
    monkeypatch.setattr(source, "_fetch_detail", lambda *args, **kwargs: {})

    candidates, metrics = source.collect_euronext_v1_3(
        date(2023, 1, 1), date(2026, 8, 20), max_pages=5
    )

    assert len(candidates) == 1
    assert metrics["status"] == "PARTIAL"
    assert metrics["pagination_complete"] is False
    assert metrics["stop_reason"] == "PAGE_FETCH_FAILED"


def test_invalid_page_cap_fails_closed() -> None:
    with pytest.raises(ValueError, match="EURONEXT_MAX_PAGES_MUST_BE_POSITIVE"):
        source.collect_euronext_v1_3(date(2023, 1, 1), date(2026, 8, 20), max_pages=0)
