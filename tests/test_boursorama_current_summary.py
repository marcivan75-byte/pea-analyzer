from __future__ import annotations

from v182.sources.boursorama_current_summary import parse_current_summary


def test_current_summary_extracts_current_forward_per_and_yield():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/cours/consensus/1rPAIR/" /></head><body>
    <div>NL0000235190 AIR</div>
    <div>dernier échange 10.08.26 / 17:35:03</div>
    <div>rendement estimé 2026</div><div>1,62%</div>
    <div>PER estimé 2026</div><div>29,16</div>
    </body></html>
    """
    obs, failures = parse_current_summary(html, {"NL0000235190"}, "airbus.html")
    assert failures == []
    by_field = {row["field"]: row for row in obs}
    assert by_field["boursorama_per_forward_current"]["value"] == 29.16
    assert by_field["per_forward_v21"]["value"] == 29.16
    assert by_field["boursorama_dividend_yield_forward_current_pct"]["value"] == 1.62
    assert by_field["dividend_yield_v21_pct"]["value"] == 1.62
    assert by_field["per_forward_v21"]["validation_status"] == "ATTRIBUTED"
    assert by_field["per_forward_v21"]["source"] == "Boursorama/FactSet"
    assert by_field["per_forward_v21"]["as_of"] == "2026-08-10"
