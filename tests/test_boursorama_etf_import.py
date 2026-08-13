from __future__ import annotations

import pandas as pd

from v182.sources.boursorama_etf_import import parse_etf_html_safe


def _etfs():
    return pd.DataFrame([{"isin":"FR0013412020","name":"ETF TEST"}])


def test_graphical_slash_7_is_not_misread_as_risk_7():
    html="""
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/trackers/recherche/" /></head><body>
    <div>ETF Morningstar</div>
    <table><thead><tr><th>ISIN</th><th>Libellé</th><th>Perf. 1 an</th><th>Risque</th><th>Morningstar</th></tr></thead>
    <tbody><tr><td>FR0013412020</td><td>ETF TEST</td><td>12,4%</td><td>/7</td><td>4</td></tr></tbody></table>
    </body></html>
    """
    obs,failures,stats=parse_etf_html_safe(html,_etfs(),"etf.html")
    fields={o["field"]:o["value"] for o in obs}
    assert fields["morningstar_rating"] == 4.0
    assert "risk_indicator" not in fields
    assert stats["risk_unobserved_rows"] == 1
    assert any(f["reason"]=="ETF_RISK_GRAPHIC_NUMERATOR_NOT_OBSERVED" for f in failures)


def test_explicit_risk_ratio_is_retained():
    html="""
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/trackers/recherche/" /></head><body>
    <div>ETF Morningstar</div>
    <table><thead><tr><th>ISIN</th><th>Libellé</th><th>Risque</th><th>Morningstar</th></tr></thead>
    <tbody><tr><td>FR0013412020</td><td>ETF TEST</td><td>6/7</td><td>3</td></tr></tbody></table>
    </body></html>
    """
    obs,failures,stats=parse_etf_html_safe(html,_etfs(),"etf.html")
    fields={o["field"]:o["value"] for o in obs}
    assert fields["morningstar_rating"] == 3.0
    assert fields["risk_indicator"] == 6.0
    assert stats["explicit_risk_rows"] == 1
    assert not any(f.get("reason")=="ETF_RISK_GRAPHIC_NUMERATOR_NOT_OBSERVED" for f in failures)
