from __future__ import annotations

from datetime import date

import requests

from v182.sources import euronext_ipo_v1_3 as source


class _Response:
    def __init__(self, html: str, status_code: int = 200):
        self.text = html
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _page(rows: list[tuple[str, str, str, str, str, str]]) -> str:
    body = "".join(
        "<tr>"
        f"<td>{day}</td><td>{name}</td><td>{ticker}</td><td>{isin}</td>"
        f"<td>{location}</td><td>{market}</td>"
        "</tr>"
        for day, name, ticker, isin, location, market in rows
    )
    return (
        "<html><body><table>"
        "<thead><tr><th>Date</th><th>Company name</th><th>Ticker</th>"
        "<th>ISIN code</th><th>Location</th><th>Market</th></tr></thead>"
        f"<tbody>{body}</tbody></table></body></html>"
    )


def test_collect_paginates_until_page_is_before_start(monkeypatch):
    pages = {
        source.EURONEXT_IPO_ALL: _page([
            ("14/07/2026", "LE SLIP FRANCAIS", "ALLSF", "FR0014018Y10", "Paris", "Euronext Growth"),
        ]),
        f"{source.EURONEXT_IPO_ALL}?page=1": _page([
            ("25/01/2023", "DEODATO.GALLERY", "ART", "IT0005528937", "Milan", "Euronext Growth"),
        ]),
        f"{source.EURONEXT_IPO_ALL}?page=2": _page([
            ("29/12/2022", "DOTSTAY", "DOT", "IT0005523839", "Milan", "Euronext Growth"),
        ]),
    }
    calls: list[str] = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return _Response(pages[url])

    monkeypatch.setattr(source.requests, "get", fake_get)

    candidates, metrics = source.collect_euronext_v1_3(
        date(2023, 1, 1), date(2026, 8, 21), max_pages=10
    )

    assert metrics["status"] == "SUCCESS"
    assert metrics["pages_fetched"] == 3
    assert metrics["stop_reason"] == "PAGE_ENTIRELY_BEFORE_START"
    assert [row["isin"] for row in candidates] == ["FR0014018Y10", "IT0005528937"]
    assert calls == [
        source.EURONEXT_IPO_ALL,
        f"{source.EURONEXT_IPO_ALL}?page=1",
        f"{source.EURONEXT_IPO_ALL}?page=2",
    ]


def test_collect_stops_on_repeated_page(monkeypatch):
    html = _page([
        ("25/01/2023", "DEODATO.GALLERY", "ART", "IT0005528937", "Milan", "Euronext Growth"),
    ])
    monkeypatch.setattr(source.requests, "get", lambda *_args, **_kwargs: _Response(html))

    candidates, metrics = source.collect_euronext_v1_3(
        date(2023, 1, 1), date(2026, 8, 21), max_pages=10
    )

    assert metrics["status"] == "SUCCESS"
    assert metrics["pages_fetched"] == 2
    assert metrics["stop_reason"] == "REPEATED_PAGE"
    assert len(candidates) == 1


def test_collect_discards_partial_results_if_later_page_fails(monkeypatch):
    page0 = _page([
        ("25/01/2023", "DEODATO.GALLERY", "ART", "IT0005528937", "Milan", "Euronext Growth"),
    ])

    def fake_get(url, **_kwargs):
        if url == source.EURONEXT_IPO_ALL:
            return _Response(page0)
        return _Response("failure", status_code=503)

    monkeypatch.setattr(source.requests, "get", fake_get)

    candidates, metrics = source.collect_euronext_v1_3(
        date(2023, 1, 1), date(2026, 8, 21), max_pages=10
    )

    assert candidates == []
    assert metrics["status"] == "FAILED"
    assert metrics["partial_candidates_discarded"] == 1
    assert metrics["partial_results_published"] is False


def test_collect_fails_closed_if_max_pages_reached(monkeypatch):
    def fake_get(url, **_kwargs):
        page_number = 0 if "?page=" not in url else int(url.rsplit("=", 1)[1])
        day = 20 - page_number
        return _Response(_page([
            (f"{day:02d}/07/2026", f"COMPANY {page_number}", f"C{page_number}", f"FR0000000{page_number:03d}", "Paris", "Euronext"),
        ]))

    monkeypatch.setattr(source.requests, "get", fake_get)

    candidates, metrics = source.collect_euronext_v1_3(
        date(2023, 1, 1), date(2026, 8, 21), max_pages=2
    )

    assert candidates == []
    assert metrics["status"] == "FAILED"
    assert "MAX_PAGES_REACHED" in metrics["detail"]
    assert metrics["partial_candidates_discarded"] == 2


def test_invalid_range_is_rejected_before_network():
    try:
        source.collect_euronext_v1_3(date(2026, 1, 2), date(2026, 1, 1))
    except ValueError as exc:
        assert str(exc) == "EURONEXT_IPO_INVALID_DATE_RANGE"
    else:
        raise AssertionError("invalid range must fail")
