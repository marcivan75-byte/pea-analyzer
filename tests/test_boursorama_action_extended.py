from __future__ import annotations

import pandas as pd

from v182.sources.boursorama_action_extended import (
    parse_dividend_calendar_html,
    parse_key_figures_html,
    parse_per_palmares_html,
    parse_profile_html,
    parse_technical_html,
)


def _actions() -> pd.DataFrame:
    return pd.DataFrame([
        {"isin": "NL0000235190", "name": "AIRBUS", "yahoo_ticker": "AIR.PA"},
        {"isin": "FR0000121014", "name": "LVMH", "yahoo_ticker": "MC.PA"},
    ])


def test_profile_extracts_safe_canonical_and_context_fields():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/cours/societe/profil/1rPAIR/" /></head><body>
      <div>ISIN NL0000235190</div>
      <div>Dernier échange</div><div>12.08.26 / 17:35</div>
      <div>Secteur</div><div>Aéronautique et défense</div>
      <div>Indice de référence</div><div>CAC 40</div>
      <div>Ouverture</div><div>211,20</div>
      <div>Clôture veille</div><div>210,10</div>
      <div>+ Haut</div><div>214,00</div>
      <div>+ Bas</div><div>209,80</div>
      <div>Volume</div><div>1 250 000</div>
      <div>Capital échangé</div><div>0,16%</div>
      <div>Valorisation</div><div>170 064 M EUR</div>
      <div>PER estimé</div><div>27,76</div>
      <div>Rendement estimé</div><div>1,70%</div>
      <div>Dernier dividende</div><div>3,20 EUR</div>
      <div>Date dernier dividende</div><div>22/04/2026</div>
      <div>Effectif</div><div>156 921</div>
      <div>Nombre de titres</div><div>790 000 000</div>
      <div>Marché</div><div>Compartiment A</div>
      <div>Eligibilité PEA : PEA</div>
      <div>Risque ESG 20,4/100</div>
    </body></html>
    """
    obs, failures, stats = parse_profile_html(html, _actions(), "airbus_profile.html")
    assert failures == []
    assert stats["isin"] == "NL0000235190"
    fields = {row["field"]: row for row in obs}
    assert fields["sector_v21"]["value"] == "Aéronautique et défense"
    assert fields["market_cap"]["value"] == 170_064_000_000.0
    assert fields["per_forward_v21"]["value"] == 27.76
    assert fields["dividend_yield_v21_pct"]["value"] == 1.70
    assert fields["boursorama_volume"]["value"] == 1_250_000.0
    assert fields["boursorama_last_dividend_date"]["value"] == "2026-04-22"
    assert fields["boursorama_pea_eligibility_observed"]["value"] is True
    assert fields["morningstar_sustainalytics_esg_risk_bourso"]["value"] == 20.4
    assert fields["per_forward_v21"]["evidence_level"] == "B"


def test_key_figures_extract_history_and_yoy_as_context_only():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/cours/societe/chiffres-cles/1rPAIR/" /></head><body>
      <div>ISIN NL0000235190</div><div>Mise à jour 12.08.26</div>
      <table><thead><tr><th></th><th>2024</th><th>2025</th></tr></thead><tbody>
        <tr><td>Chiffre d'affaires</td><td>70 000</td><td>77 000</td></tr>
        <tr><td>Résultat opérationnel</td><td>6 000</td><td>6 600</td></tr>
        <tr><td>Résultat net</td><td>4 000</td><td>4 400</td></tr>
        <tr><td>Résultat net (part du groupe)</td><td>3 900</td><td>4 290</td></tr>
      </tbody></table>
      <table><thead><tr><th></th><th>2024</th><th>2025</th></tr></thead><tbody>
        <tr><td>Dettes financières courantes</td><td>3 000</td><td>3 300</td></tr>
        <tr><td>Dettes financières non courantes</td><td>10 000</td><td>9 500</td></tr>
        <tr><td>Total actif</td><td>120 000</td><td>130 000</td></tr>
        <tr><td>Total passif</td><td>120 000</td><td>130 000</td></tr>
      </tbody></table>
      <table><thead><tr><th></th><th>2024</th><th>2025</th></tr></thead><tbody>
        <tr><td>Résultat net part du groupe par action</td><td>5,10</td><td>5,60</td></tr>
        <tr><td>Résultat net part du groupe dilué par action</td><td>5,05</td><td>5,55</td></tr>
        <tr><td>Marge opérationnelle</td><td>8,57</td><td>8,90</td></tr>
        <tr><td>Rentabilité financière</td><td>18,2</td><td>19,1</td></tr>
        <tr><td>Ratio d'endettement</td><td>41,0</td><td>38,5</td></tr>
        <tr><td>Effectif en fin d'année</td><td>150 000</td><td>156 000</td></tr>
      </tbody></table>
      <table><thead><tr><th></th><th>2025</th><th>2026</th></tr></thead><tbody>
        <tr><td>Chiffre d'affaires 1er trimestre</td><td>17 000</td><td>18 700</td></tr>
        <tr><td>Chiffre d'affaires de l'année</td><td>77 000</td><td>0</td></tr>
      </tbody></table>
    </body></html>
    """
    obs, failures, _ = parse_key_figures_html(html, _actions(), "airbus_key.html")
    assert failures == []
    fields = {row["field"]: row for row in obs}
    assert fields["boursorama_actual_revenue_k_eur"]["value"] == 77_000.0
    assert fields["boursorama_actual_revenue_k_eur_yoy_pct"]["value"] == 10.0
    assert fields["boursorama_total_financial_debt_k_eur"]["value"] == 12_800.0
    assert fields["boursorama_operating_margin_pct"]["value"] == 8.9
    assert fields["boursorama_return_on_equity_pct"]["value"] == 19.1
    assert fields["boursorama_revenue_q1_current_k_eur"]["value"] == 18_700.0
    assert fields["boursorama_revenue_q1_yoy_pct"]["value"] == 10.0
    assert "roe_api" not in fields
    assert "revenue_growth_yf" not in fields


def test_per_palmares_can_fill_existing_forward_per_without_fuzzy_mapping():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/actions/palmares/per/" /></head><body>
      <table><thead><tr><th>Libellé</th><th>PER 2025</th><th>BNA 2025</th><th>PER 2026</th><th>BNA 2026</th><th>PER 2027</th><th>BNA 2027</th></tr></thead>
      <tbody><tr><td>AIRBUS</td><td>29,35</td><td>6,89</td><td>27,76</td><td>7,29</td><td>23,40</td><td>8,64</td></tr></tbody></table>
    </body></html>
    """
    obs, failures, stats = parse_per_palmares_html(html, _actions(), "per.html")
    assert failures == []
    assert stats["matched_rows"] == 1
    fields = {row["field"]: row["value"] for row in obs}
    assert fields["per_forward_v21"] == 27.76
    assert fields["boursorama_bna_2027_eur"] == 8.64


def test_dividend_calendar_keeps_event_yield_context_only():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/actualites/calendriers/dividendes" /></head><body>
      <table><thead><tr><th>Date</th><th>Société</th><th>Évènement</th><th>Montant</th><th>Rendement</th></tr></thead>
      <tbody><tr><td>20 août</td><td>AIRBUS</td><td>Détachement</td><td>1,50 EUR</td><td>0,70%</td></tr></tbody></table>
    </body></html>
    """
    obs, failures, stats = parse_dividend_calendar_html(html, _actions(), "divcal.html")
    assert failures == []
    assert stats["matched_rows"] == 1
    fields = {row["field"]: row["value"] for row in obs}
    assert fields["boursorama_next_dividend_event_date"] == "2026-08-20"
    assert fields["boursorama_next_dividend_amount_eur"] == 1.5
    assert fields["boursorama_next_dividend_event_yield_pct"] == 0.7
    assert "dividend_yield_v21_pct" not in fields


def test_tec_summary_remains_c_evidence_context_not_canonical_technical():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/cours/analyses/1rPAIR/" /></head><body>
      <div>ISIN NL0000235190</div>
      <div>SYNTHESE Le MACD est positif et supérieur à sa ligne de signal. Le RSI montre un surachat. Le stochastique indique une survente. information fournie par TEC 12.08.2026</div>
    </body></html>
    """
    obs, failures, stats = parse_technical_html(html, _actions(), "tec.html")
    assert failures == []
    assert stats["matched_rows"] == 1
    fields = {row["field"]: row for row in obs}
    assert fields["boursorama_tec_macd_positive_flag"]["value"] == 1.0
    assert fields["boursorama_tec_macd_above_signal_flag"]["value"] == 1.0
    assert fields["boursorama_tec_rsi_overbought_flag"]["value"] == 1.0
    assert fields["boursorama_tec_stoch_oversold_flag"]["value"] == 1.0
    assert fields["boursorama_tec_summary"]["evidence_level"] == "C"
    assert "macd" not in fields
    assert "rsi14" not in fields
