from __future__ import annotations

import pandas as pd

from v182.sources.boursorama_bulk_import import parse_consensus_bulk_html


def test_bulk_consensus_maps_by_boursorama_link_and_extracts_factset_fields():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/actions/consensus/recommandations-paris/" /></head><body>
      <table>
        <thead><tr><th>Libellé</th><th>Reco.</th><th>Der. Cours*</th><th>Obj. Cours**</th><th>Potentiel</th><th>Nb. Analystes.</th><th>Bna. 2026</th><th>Rend. 2026***</th><th>Per. 2026</th><th>Per. 2025</th></tr></thead>
        <tbody>
          <tr><td><a href="/cours/1rPAIR/">AIRBUS</a></td><td>Acheter</td><td>213,600</td><td>224,043</td><td>+4,889%</td><td>28(+1)</td><td>7,285 EUR</td><td>1,699%</td><td>27,756</td><td>29,355</td></tr>
          <tr><td><a href="/cours/1rPBNP/">BNP PARIBAS</a></td><td>Acheter</td><td>112,440</td><td>115,708</td><td>+2,906%</td><td>22(+0)</td><td>11,540 EUR</td><td>5,462%</td><td>9,503</td><td>10,657</td></tr>
        </tbody>
      </table>
      <div>Source : Factset JCF</div><div>Mis à jour le 07.08.26</div>
    </body></html>
    """
    actions = pd.DataFrame([
        {"isin": "NL0000235190", "name": "AIRBUS", "yahoo_ticker": "AIR.PA"},
        {"isin": "FR0000131104", "name": "BNP PARIBAS", "yahoo_ticker": "BNP.PA"},
    ])
    obs, failures, stats = parse_consensus_bulk_html(html, actions, "bulk.html")
    assert failures == []
    assert stats["matched_rows"] == 2
    air = {row["field"]: row["value"] for row in obs if row["isin"] == "NL0000235190"}
    assert air["boursorama_bulk_recommendation"] == "Acheter"
    assert air["boursorama_target_price"] == 224.043
    assert air["target_upside_pct_v21"] == 4.889
    assert air["boursorama_consensus_analysts"] == 28.0
    assert air["boursorama_eps_forward_1y"] == 7.285
    assert air["dividend_yield_v21_pct"] == 1.699
    assert air["per_forward_v21"] == 27.756
    assert air["boursorama_per_reported"] == 29.355


def test_bulk_consensus_does_not_guess_unmatched_names():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/actions/consensus/recommandations-paris/" /></head><body>
      <table><thead><tr><th>Libellé</th><th>Reco.</th><th>Der. Cours*</th><th>Obj. Cours**</th><th>Potentiel</th><th>Nb. Analystes.</th><th>Bna. 2026</th><th>Rend. 2026***</th><th>Per. 2026</th><th>Per. 2025</th></tr></thead>
      <tbody><tr><td>SOCIETE INCONNUE</td><td>Acheter</td><td>10</td><td>12</td><td>20%</td><td>4</td><td>1</td><td>2%</td><td>10</td><td>11</td></tr></tbody></table>
      <div>Mis à jour le 07.08.26</div>
    </body></html>
    """
    actions = pd.DataFrame([{"isin": "NL0000235190", "name": "AIRBUS", "yahoo_ticker": "AIR.PA"}])
    obs, failures, stats = parse_consensus_bulk_html(html, actions)
    assert obs == []
    assert stats["matched_rows"] == 0
    assert failures[0]["reason"] == "BULK_NAME_OR_TICKER_NOT_MATCHED"
