from __future__ import annotations

import pandas as pd

from v182.sources.boursorama_etf_import import parse_etf_detail_html


def test_etf_detail_extracts_structure_aum_performance_and_holdings_without_ter_guess():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/trackers/cours/1rTWPEA/" /></head><body>
      <div>ISIN FR001400U5Q4</div>
      <div>Indice de référence</div><div>MSCI World</div>
      <div>Catégorie Morningstar</div><div>Actions Internationales Gdes Cap. Mixte</div>
      <div>Date de création</div><div>15/03/2024</div>
      <div>Société de gestion</div><div>Amundi Asset Management</div>
      <div>Gérants</div><div>Equipe indicielle</div>
      <div>Forme juridique</div><div>ETF</div>
      <div>Classe d'actifs</div><div>Actions</div>
      <div>Zone géographique</div><div>Monde</div>
      <div>Affectation des résultats</div><div>Capitalisation</div>
      <div>Réplication</div><div>Physique</div>
      <div>Frais de gestion maximum</div><div>0,25%</div>
      <div>Actif net (EUR) 1 250 M / 12.08.2026</div>
      <div>Risque du fonds (SRI) 4 / 7</div>
      <div>Portefeuille</div>
      <div>Eligibilité : PEA</div>
      <div>Composition (les 10 premières lignes)
        NVIDIA 5,2%
        APPLE 4,8%
        MICROSOFT 4,1%
        AMAZON 2,7%
        META 2,0%
        BROADCOM 1,7%
        ALPHABET A 1,6%
        TESLA 1,4%
        ALPHABET C 1,3%
        JPMORGAN 1,0%
        Date du portefeuille : 31.07.2026
      </div>
      <div>Données calculées par Morningstar au : 31.07.2026</div>
      <table>
        <thead><tr><th></th><th>1er janv.</th><th>1 mois</th><th>3 mois</th><th>6 mois</th><th>1 an</th><th>3 ans</th></tr></thead>
        <tbody>
          <tr><td>ETF</td><td>8,1%</td><td>2,0%</td><td>5,5%</td><td>7,0%</td><td>15,2%</td><td>35,0%</td></tr>
          <tr><td>CAT. MORNING*</td><td>7,2%</td><td>1,6%</td><td>4,8%</td><td>6,1%</td><td>13,0%</td><td>30,0%</td></tr>
          <tr><td>RANG</td><td>35</td><td>30</td><td>28</td><td>25</td><td>20</td><td>18</td></tr>
        </tbody>
      </table>
      <table>
        <thead><tr><th></th><th>2024</th><th>2025</th><th>2026</th></tr></thead>
        <tbody>
          <tr><td>TRACKER</td><td>12,0%</td><td>18,0%</td><td>8,1%</td></tr>
          <tr><td>CAT. MORNING*</td><td>10,0%</td><td>15,0%</td><td>7,2%</td></tr>
          <tr><td>RANG</td><td>40</td><td>25</td><td>35</td></tr>
        </tbody>
      </table>
    </body></html>
    """
    etfs = pd.DataFrame([{"isin": "FR001400U5Q4", "name": "AMUNDI PEA MONDE MSCI WORLD"}])
    obs, failures, stats = parse_etf_detail_html(html, etfs, "wpea.html")
    assert failures == []
    assert stats["matched_rows"] == 1
    fields = {row["field"]: row for row in obs}
    assert fields["morningstar_category"]["value"] == "Actions Internationales Gdes Cap. Mixte"
    assert fields["boursorama_reference_index"]["value"] == "MSCI World"
    assert fields["boursorama_replication"]["value"] == "Physique"
    assert fields["boursorama_management_fee_max_pct"]["value"] == 0.25
    assert fields["boursorama_net_assets_eur_m"]["value"] == 1250.0
    assert fields["risk_indicator"]["value"] == 4.0
    assert fields["boursorama_pea_button_observed"]["value"] is True
    assert fields["boursorama_perf_1y_pct"]["value"] == 15.2
    assert fields["boursorama_morningstar_category_perf_1y_pct"]["value"] == 13.0
    assert fields["boursorama_morningstar_rank_1y"]["value"] == 20.0
    assert fields["boursorama_perf_calendar_2025_pct"]["value"] == 18.0
    assert fields["boursorama_top10_holdings_count"]["value"] == 10
    assert fields["boursorama_portfolio_date"]["value"] == "2026-07-31"
    assert fields["boursorama_morningstar_data_date"]["value"] == "2026-07-31"
    assert "ter" not in fields
    assert "ter_pct" not in fields


def test_etf_detail_does_not_infer_risk_from_denominator_only():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/trackers/cours/1rTTEST/" /></head><body>
      <div>ISIN FR001400U5Q4</div>
      <div>Risque du fonds (SRI) /7</div><div>Portefeuille</div>
      <div>Frais de gestion maximum</div><div>0,20%</div>
    </body></html>
    """
    etfs = pd.DataFrame([{"isin": "FR001400U5Q4", "name": "ETF TEST"}])
    obs, failures, _ = parse_etf_detail_html(html, etfs, "risk.html")
    assert failures == []
    fields = {row["field"]: row for row in obs}
    assert "risk_indicator" not in fields
    assert "boursorama_risk_indicator" not in fields
    assert fields["boursorama_management_fee_max_pct"]["value"] == 0.20
