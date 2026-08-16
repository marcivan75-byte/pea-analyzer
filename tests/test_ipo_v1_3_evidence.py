from __future__ import annotations

from v182.sources import euronext_ipo_v1_3 as euronext
from v182.sources import ipo_peers_v1_3 as peers


def test_euronext_showcase_parser_extracts_only_official_fields() -> None:
    html = """
    <html><body>
      <h1>Polar Resources AS</h1>
      <div>Symbol</div><div>POLAR</div>
      <div>ISIN code</div><div>NO0013756361</div>
      <div>Exchange / Market</div><div>Euronext Growth</div>
      <div>Trading location</div><div>Oslo</div>
      <div>ICB</div><div>55102000 General Mining</div>
      <div>Website address</div><div>www.polarresources.no</div>
      <div>IPO date</div><div>Thu 09/07/2026</div>
      <div>IPO price</div><div>Reference price NOK 7</div>
      <div>IPO type</div><div>Private placement</div>
    </body></html>
    """
    result = euronext.parse_showcase_detail(html, "https://live.euronext.com/en/ipo-showcase/polar-resources")
    assert result["euronext_detail_status"] == "SUCCESS"
    assert result["euronext_icb_code"] == "55102000"
    assert result["euronext_icb_name"] == "General Mining"
    assert result["euronext_ipo_price"] == 7.0
    assert result["euronext_ipo_currency"] == "NOK"
    assert result["euronext_ipo_type"] == "Private placement"
    assert result["euronext_website"] == "www.polarresources.no"


def test_euronext_price_parser_supports_euro_and_decimal_comma() -> None:
    price, currency = euronext.parse_ipo_price("Prix de l'offre 14,80 € par action")
    assert price == 14.8
    assert currency == "EUR"


def test_euronext_showcase_link_map_uses_official_href() -> None:
    html = '<a href="/en/ipo-showcase/polar-resources">Polar Resources AS</a>'
    links = euronext._showcase_links(html)
    assert links[euronext._norm("Polar Resources AS")] == "https://live.euronext.com/en/ipo-showcase/polar-resources"


def test_peer_metric_requires_annual_same_basis() -> None:
    assert peers.extract_annual_price_sales({"metric": {"psTTM": 3.2}}) is None
    assert peers.extract_annual_price_sales({"metric": {"psAnnual": 4.5, "psTTM": 3.2}}) == 4.5


def test_real_peer_median_gate_scores_only_with_three_valid_peers(monkeypatch) -> None:
    payloads = {
        "AAA": {"metric": {"psAnnual": 3.0}},
        "BBB": {"metric": {"psAnnual": 4.0}},
        "CCC": {"metric": {"psAnnual": 5.0}},
        "DDD": {"metric": {"psTTM": 2.0}},
    }
    def fake_get(path: str, params: dict, api_key: str, timeout: int):
        assert api_key == "test-key"
        if path == "/stock/peers":
            return ["IPO", "AAA", "BBB", "CCC", "DDD"]
        return payloads[params["symbol"]]
    monkeypatch.setattr(peers, "_get_json", fake_get)
    monkeypatch.setattr(peers.time, "sleep", lambda _seconds: None)
    result = peers.build_peer_benchmark("IPO", 2.0, "test-key")
    assert result.status == "SUCCESS"
    assert result.peer_count == 3
    assert result.peer_ps_median == 4.0
    assert result.candidate_to_peer_ratio == 0.5
    assert result.score == 95.0


def test_real_peer_gate_refuses_insufficient_or_missing_evidence(monkeypatch) -> None:
    def fake_get(path: str, params: dict, api_key: str, timeout: int):
        if path == "/stock/peers":
            return ["IPO", "AAA", "BBB"]
        return {"metric": {"psAnnual": 4.0}}
    monkeypatch.setattr(peers, "_get_json", fake_get)
    monkeypatch.setattr(peers.time, "sleep", lambda _seconds: None)
    insufficient = peers.build_peer_benchmark("IPO", 2.0, "test-key")
    assert insufficient.status == "INSUFFICIENT_VALID_PEERS"
    assert insufficient.score is None
    missing = peers.build_peer_benchmark("IPO", None, "test-key")
    assert missing.status == "SKIPPED_NO_CANDIDATE_ANNUAL_PS"
    assert missing.score is None


def test_peer_score_populates_opportunity_and_inverse_risk_only_on_success(monkeypatch) -> None:
    benchmark = peers.PeerBenchmark(
        status="SUCCESS", score=76.0, candidate_ps=4.0, peer_ps_median=4.0,
        candidate_to_peer_ratio=1.0, peer_count=4, peer_symbols=("A", "B", "C", "D"), grouping="industry",
    )
    monkeypatch.setattr(peers, "build_peer_benchmark", lambda *args, **kwargs: benchmark)
    candidate = peers.add_peer_evidence({"symbol": "IPO", "sec_ipo_price_to_sales": 4.0}, "key")
    assert candidate["opportunity_valuation_vs_peers"] == 76.0
    assert candidate["risk_valuation"] == 24.0
    assert candidate["peer_valuation_source"] == "FINNHUB_REAL_PEERS_ANNUAL_PS"
