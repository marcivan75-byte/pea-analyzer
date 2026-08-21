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


def test_catalogue_dates_are_explicitly_day_first():
    assert source._parse_catalogue_date("10/02/2023") == date(2023, 2, 10)
    assert source._parse_catalogue_date("02/10/2023") == date(2023, 10, 2)
    assert source._parse_catalogue_date("2023-02-10") == date(2023, 2, 10)


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


def test_target_filter_skips_non_target_detail_fetches(monkeypatch):
    pages = {
        source.EURONEXT_IPO_ALL: _page([
            ("25/01/2023", "DEODATO.GALLERY", "ART", "IT0005528937", "Milan", "Euronext Growth"),
            ("24/01/2023", "NOT TARGET", "NOPE", "FR0000000001", "Paris", "Euronext"),
        ]),
        f"{source.EURONEXT_IPO_ALL}?page=1": _page([
            ("29/12/2022", "OLD COMPANY", "OLD", "FR0000000002", "Paris", "Euronext"),
        ]),
    }
    detail_calls: list[str] = []

    def fake_get(url, **_kwargs):
        return _Response(pages[url])

    def fake_detail(url, _headers, _timeout):
        detail_calls.append(url)
        return {"euronext_showcase_url": url, "euronext_detail_status": "SUCCESS"}

    monkeypatch.setattr(source.requests, "get", fake_get)
    monkeypatch.setattr(source, "_showcase_links", lambda _html: {
        source._norm("DEODATO.GALLERY"): "https://live.euronext.com/en/ipo-showcase/deodato",
        source._norm("NOT TARGET"): "https://live.euronext.com/en/ipo-showcase/not-target",
    })
    monkeypatch.setattr(source, "_fetch_detail", fake_detail)

    candidates, metrics = source.collect_euronext_v1_3(
        date(2023, 1, 1),
        date(2026, 8, 21),
        max_pages=10,
        target_isins={"it0005528937"},
    )

    assert [row["isin"] for row in candidates] == ["IT0005528937"]
    assert detail_calls == ["https://live.euronext.com/en/ipo-showcase/deodato"]
    assert metrics["target_filter_active"] is True
    assert metrics["target_isins_requested"] == 1
    assert metrics["non_target_rows_skipped"] == 1


def test_listing_only_mode_uses_catalogue_page_without_detail_fetch(monkeypatch):
    pages = {
        source.EURONEXT_IPO_ALL: _page([
            ("10/02/2023", "EUROGROUP LAMINATIONS", "EGLA", "IT0005527616", "Milan", "Euronext Growth"),
        ]),
        f"{source.EURONEXT_IPO_ALL}?page=1": _page([
            ("29/12/2022", "OLD COMPANY", "OLD", "FR0000000002", "Paris", "Euronext"),
        ]),
    }
    detail_calls: list[str] = []
    monkeypatch.setattr(source.requests, "get", lambda url, **_kwargs: _Response(pages[url]))
    monkeypatch.setattr(source, "_fetch_detail", lambda url, *_args: detail_calls.append(url) or {})

    candidates, metrics = source.collect_euronext_v1_3(
        date(2023, 1, 1),
        date(2026, 8, 21),
        max_pages=10,
        target_isins={"IT0005527616"},
        enrich_details=False,
    )

    assert len(candidates) == 1
    assert candidates[0]["isin"] == "IT0005527616"
    assert candidates[0]["expected_date"] == "2023-02-10"
    assert candidates[0]["euronext_showcase_url"] == source.EURONEXT_IPO_ALL
    assert detail_calls == []
    assert metrics["detail_enrichment_enabled"] is False
    assert metrics["date_parse_policy"] == "EURONEXT_DD_MM_YYYY_DAY_FIRST"


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
