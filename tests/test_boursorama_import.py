from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.io.frames import apply_observations
from v182.sources.boursorama_import import parse_action_html, parse_etf_html


def _action_html() -> str:
    return """
    <html><head><link rel="canonical" href="https://www.boursorama.com/cours/consensus/1rPAIR/" /></head><body>
    <div>ISIN NL0000235190</div>
    <div>secteur <a>Aérospatiale</a></div>
    <div>valorisation 170 064 MEUR</div>
    <div>PER estimé 2026 29,16</div>
    <div>Risque ESG 20,4/100 (moyen)</div>
    <div>PEA</div>
    <div>Potentiel : 7,25%</div>
    <table>
      <thead><tr><th>Opinion</th><th>Il y a 3 mois</th><th>Il y a 2 mois</th><th>Il y a 1 mois</th><th>Il y a 7 jours</th><th>le 10/08/2026</th></tr></thead>
      <tbody>
        <tr><td>1. Acheter</td><td>14</td><td>14</td><td>13</td><td>15</td><td>15</td></tr>
        <tr><td>2. Renforcer</td><td>4</td><td>4</td><td>4</td><td>5</td><td>5</td></tr>
        <tr><td>3. Conserver</td><td>9</td><td>9</td><td>8</td><td>7</td><td>6</td></tr>
        <tr><td>4. Alléger</td><td>0</td><td>0</td><td>0</td><td>0</td><td>1</td></tr>
        <tr><td>5. Vendre</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
        <tr><td>Nombre d'analystes</td><td>27</td><td>27</td><td>25</td><td>27</td><td>27</td></tr>
        <tr><td>Note médiane</td><td>1,81</td><td>1,81</td><td>1,80</td><td>1,70</td><td>1,74</td></tr>
        <tr><td>Historique des objectifs de cours médian (en EUR)</td><td>211,44</td><td>209,84</td><td>209,48</td><td>216,17</td><td>218,80</td></tr>
      </tbody>
    </table>
    <table>
      <thead><tr><th></th><th>2025</th><th>Estimation 2026</th><th>Estimation 2027</th></tr></thead>
      <tbody>
        <tr><td>Bénéfice net par action</td><td>6,89 EUR</td><td>7,29 EUR</td><td>8,64 EUR</td></tr>
        <tr><td>PER</td><td>29,35</td><td>27,76</td><td>23,40</td></tr>
        <tr><td>Dividende par action</td><td>3,20 EUR</td><td>3,44 EUR</td><td>4,08 EUR</td></tr>
        <tr><td>Rendement</td><td>1,58%</td><td>1,70%</td><td>2,02%</td></tr>
      </tbody>
    </table>
    <table>
      <thead><tr><th></th><th>2025</th><th>Estimation 2026</th><th>Estimation 2027</th></tr></thead>
      <tbody>
        <tr><td>Chiffre d'affaires</td><td>73 420</td><td>80 438</td><td>90 109</td></tr>
        <tr><td>EBITDA</td><td>10 261</td><td>10 762</td><td>12 374</td></tr>
        <tr><td>EBIT</td><td>7 128</td><td>7 601</td><td>9 041</td></tr>
        <tr><td>Dette financière nette</td><td>-12 171</td><td>-13 260</td><td>-15 242</td></tr>
        <tr><td>Actif net par action</td><td>33,08</td><td>37,04</td><td>41,94</td></tr>
        <tr><td>Cash Flow par action</td><td>10,15</td><td>10,98</td><td>13,10</td></tr>
      </tbody>
    </table>
    </body></html>
    """


def test_action_html_extracts_high_value_fields_without_duplicate_current_per_semantics():
    obs, failures, stats = parse_action_html(
        _action_html(),
        canonical_action_isins={"NL0000235190"},
        source_file="airbus.html",
    )
    assert failures == []
    assert stats["isin"] == "NL0000235190"
    by_field = {row["field"]: row for row in obs}
    assert by_field["boursorama_consensus_analysts"]["value"] == 27
    assert by_field["boursorama_consensus_note_median"]["value"] == 1.74
    assert by_field["consensus_score_100_v21"]["value"] == 81.5
    assert by_field["consensus_delta_4w"]["value"] == 0.06
    assert by_field["target_upside_pct_v21"]["value"] == 7.25
    assert by_field["boursorama_per_forward_1y"]["value"] == 27.76
    assert by_field["boursorama_dividend_yield_forward_1y_pct"]["value"] == 1.70
    assert "per_forward_v21" not in by_field
    assert "dividend_yield_v21_pct" not in by_field
    assert by_field["market_cap"]["value"] == 170_064_000_000.0
    assert by_field["boursorama_eps_forward_1y"]["value"] == 7.29
    assert by_field["boursorama_revenue_forward_1y_m"]["value"] == 80438.0
    assert by_field["morningstar_sustainalytics_esg_risk_bourso"]["value"] == 20.4
    assert by_field["consensus_score_100_v21"]["validation_status"] == "ATTRIBUTED"
    assert by_field["consensus_score_100_v21"]["evidence_level"] == "B"
    assert by_field["consensus_score_100_v21"]["source"] == "Boursorama/FactSet"


def test_etf_html_maps_morningstar_and_risk_to_existing_fields():
    html = """
    <html><head><link rel="canonical" href="https://www.boursorama.com/bourse/trackers/recherche/" /></head><body>
    <div>Trackers Morningstar ISIN Catégorie</div>
    <table>
      <thead><tr><th>ISIN</th><th>Libellé</th><th>Devise</th><th>Notation Morningstar</th><th>Catégorie Morningstar</th><th>Risque</th><th>Perf. 1 an</th></tr></thead>
      <tbody><tr><td>FR0013412020</td><td>ETF TEST</td><td>EUR</td><td>4</td><td>Actions Europe</td><td>6</td><td>12,4%</td></tr></tbody>
    </table></body></html>
    """
    etfs = pd.DataFrame([{"isin": "FR0013412020", "name": "ETF TEST"}])
    obs, failures, stats = parse_etf_html(html, etfs=etfs, source_file="etf.html")
    assert failures == []
    assert stats["rows"] == 1
    by_field = {row["field"]: row for row in obs}
    assert by_field["morningstar_rating"]["value"] == 4.0
    assert by_field["risk_indicator"]["value"] == 6.0
    assert by_field["morningstar_category"]["value"] == "Actions Europe"
    assert by_field["morningstar_rating"]["source"] == "Boursorama/Morningstar"


def test_html_without_boursorama_attribution_is_rejected():
    html = _action_html().replace(
        "https://www.boursorama.com/cours/consensus/1rPAIR/",
        "https://example.com/not-boursorama",
    )
    obs, failures, stats = parse_action_html(html, canonical_action_isins={"NL0000235190"})
    assert obs == []
    assert stats["rows"] == 0
    assert failures[0]["reason"] == "BOURSORAMA_SOURCE_URL_MISSING"


def test_boursorama_b_replaces_yfinance_c_but_not_official_a(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PEA_PROVENANCE_PATH", str(tmp_path / "provenance.csv"))
    base = pd.DataFrame([{
        "isin": "NL0000235190",
        "name": "AIRBUS",
        "per_forward_v21": pd.NA,
        "target_upside_pct_v21": pd.NA,
    }])
    yfinance = [{
        "universe": "ACTION", "isin": "NL0000235190", "field": "per_forward_v21", "value": 31.0,
        "source": "yfinance", "collected_at": "2026-08-10T12:00:00+00:00", "as_of": "2026-08-10",
        "evidence_level": "C", "validation_status": "AUTO_MATCH",
    }]
    frame, q1 = apply_observations(base, yfinance)
    assert q1 == []
    bourso = [{
        "universe": "ACTION", "isin": "NL0000235190", "field": "per_forward_v21", "value": 27.76,
        "source": "Boursorama/FactSet", "collected_at": "2026-08-11T12:00:00+00:00", "as_of": "2026-08-11",
        "evidence_level": "B", "validation_status": "ATTRIBUTED",
    }]
    frame, q2 = apply_observations(frame, bourso)
    assert q2 == []
    assert float(frame.loc[0, "per_forward_v21"]) == 27.76

    official = [{
        "universe": "ACTION", "isin": "NL0000235190", "field": "target_upside_pct_v21", "value": 8.0,
        "source": "Issuer", "collected_at": "2026-08-11T12:00:00+00:00", "as_of": "2026-08-11",
        "evidence_level": "A", "validation_status": "VALIDATED",
    }]
    frame, _ = apply_observations(frame, official)
    lower = [{
        "universe": "ACTION", "isin": "NL0000235190", "field": "target_upside_pct_v21", "value": 7.25,
        "source": "Boursorama/FactSet", "collected_at": "2026-08-12T12:00:00+00:00", "as_of": "2026-08-12",
        "evidence_level": "B", "validation_status": "ATTRIBUTED",
    }]
    frame, q3 = apply_observations(frame, lower)
    assert q3 == []
    assert float(frame.loc[0, "target_upside_pct_v21"]) == 8.0
