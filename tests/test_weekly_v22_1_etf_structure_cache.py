from __future__ import annotations

from pathlib import Path

from v182.reporting import weekly_unified_super_runner_v22_1 as runner
from v182.sources import etf_inception_data, etf_structural_data


class FakeResponse:
    def __init__(self, content: bytes = b"same-pdf") -> None:
        self.content = content
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


def test_v22_1_reuses_exact_same_url_response_and_pdf_text(monkeypatch, tmp_path: Path) -> None:
    network = {"struct": 0, "inception": 0}
    pdf = {"struct": 0, "inception": 0}
    url = "https://www.amundietf.fr/pdfDocuments/monthly-factsheet/FR0000000001/FRA/FRA/RETAIL/ETF/20260731"

    def struct_get(session, requested_url, *, timeout=25):
        network["struct"] += 1
        assert requested_url == url
        return FakeResponse()

    def inception_get(session, requested_url, *, timeout=25):
        network["inception"] += 1
        assert requested_url == url
        return FakeResponse()

    def struct_pdf(content):
        pdf["struct"] += 1
        return "FR0000000001 STRUCTURAL AND INCEPTION TEXT"

    def inception_pdf(content):
        pdf["inception"] += 1
        return "FR0000000001 STRUCTURAL AND INCEPTION TEXT"

    monkeypatch.setattr(etf_structural_data, "_get", struct_get)
    monkeypatch.setattr(etf_inception_data, "_get", inception_get)
    monkeypatch.setattr(etf_structural_data, "_pdf_text", struct_pdf)
    monkeypatch.setattr(etf_inception_data, "_pdf_text", inception_pdf)

    def fake_previous_run(root):
        first = etf_structural_data._get(object(), url)
        second = etf_inception_data._get(object(), url)
        assert first is second
        text1 = etf_structural_data._pdf_text(first.content)
        text2 = etf_inception_data._pdf_text(second.content)
        assert text1 == text2
        return {"status": "SUCCESS"}

    monkeypatch.setattr(runner.previous, "run", fake_previous_run)
    payload = runner.run(tmp_path)

    assert payload["status"] == "SUCCESS"
    assert network == {"struct": 1, "inception": 0}
    assert pdf == {"struct": 1, "inception": 0}
    audit = (tmp_path / "outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V22_1.json").read_text(encoding="utf-8")
    assert '"etf_exact_url_http_cache_hits": 1' in audit
    assert '"etf_exact_url_http_cache_misses": 1' in audit
    assert '"etf_amundi_exact_url_reuse_hits": 1' in audit
    assert '"etf_pdf_text_cache_hits": 1' in audit
    assert '"etf_pdf_text_cache_misses": 1' in audit
    assert '"etf_source_urls_changed": false' in audit
    assert '"etf_structure_collectors_remain_sequential": true' in audit


def test_v22_1_does_not_cache_failed_http_request(monkeypatch, tmp_path: Path) -> None:
    calls = {"struct": 0, "inception": 0}
    url = "https://www.amundietf.fr/pdfDocuments/monthly-factsheet/failure"

    def struct_get(session, requested_url, *, timeout=25):
        calls["struct"] += 1
        raise TimeoutError("first request failed")

    def inception_get(session, requested_url, *, timeout=25):
        calls["inception"] += 1
        return FakeResponse(b"recovered")

    monkeypatch.setattr(etf_structural_data, "_get", struct_get)
    monkeypatch.setattr(etf_inception_data, "_get", inception_get)

    def fake_previous_run(root):
        try:
            etf_structural_data._get(object(), url)
        except TimeoutError:
            pass
        response = etf_inception_data._get(object(), url)
        assert response.content == b"recovered"
        return {"status": "SUCCESS"}

    monkeypatch.setattr(runner.previous, "run", fake_previous_run)
    payload = runner.run(tmp_path)

    assert payload["status"] == "SUCCESS"
    assert calls == {"struct": 1, "inception": 1}
