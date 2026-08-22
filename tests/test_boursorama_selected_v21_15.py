from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from v182.sources.boursorama_selected import (
    collect_selected_action_context_cached,
    parse_forward_forecasts_html,
    parse_quote_context_html,
)


QUOTE_HTML = """
<html><body>
secteur Chimie de base Indice de référence CAC 40
dernière valeur 167,500 ouverture 167,220 clôture veille 167,380 + haut 168,040 + bas 166,080
volume 569 280 valorisation 106 650 MEUR rendement estimé 2026 2,14%
PER estimé 2026 26,61 dernier dividende 3,70 EUR (18/05/26)
Éligibilité SRD PEA Risque ESG 12,6/100
<table>
<tr><th>Prévisions</th><th>2025</th><th>Estim. 2026</th><th>Estim. 2027</th></tr>
<tr><td>Dividende par action</td><td>3,36 EUR</td><td>3,60 EUR / +7 %</td><td>3,90 EUR / +8 %</td></tr>
<tr><td>Rendement</td><td>1,95%</td><td>2,09%</td><td>2,27%</td></tr>
<tr><td>Bénéfice net par action</td><td>5,80 EUR</td><td>6,34 EUR / +9 %</td><td>7,15 EUR / +13 %</td></tr>
<tr><td>PER</td><td>29,73</td><td>27,18</td><td>24,11</td></tr>
</table>
<table>
<tr><th>Consensus</th><th>1 mois</th><th>22/08/2026</th></tr>
<tr><td>Acheter</td><td>14</td><td>15</td></tr>
<tr><td>Renforcer</td><td>4</td><td>4</td></tr>
<tr><td>Conserver</td><td>4</td><td>4</td></tr>
<tr><td>Alléger</td><td>0</td><td>0</td></tr>
<tr><td>Vendre</td><td>0</td><td>0</td></tr>
<tr><td>Objectif de cours médian</td><td>185,52</td><td>196,09</td></tr>
<tr><td>Potentiel</td><td>10,0%</td><td>17,70%</td></tr>
</table>
<table>
<tr><th>Période</th><th>Performance</th><th>Plus haut</th><th>Plus bas</th></tr>
<tr><td>1 semaine</td><td>1,20%</td><td>168,04</td><td>164,00</td></tr>
<tr><td>1 mois</td><td>3,40%</td><td>170,00</td><td>158,00</td></tr>
</table>
</body></html>
"""

DEEP_HTML = """
<html><body>
secteur Chimie de base Indice de référence CAC 40 Risque ESG 12,6/100 Éligibilité PEA
<table>
<tr><th>Compte de résultat</th><th>12.24</th><th>12.25</th></tr>
<tr><td>Chiffre d'affaires</td><td>27 057 800</td><td>26 940 200</td></tr>
<tr><td>Résultat net (part du groupe)</td><td>3 306 100</td><td>3 517 900</td></tr>
</table>
<table>
<tr><th>Bilan</th><th>12.24</th><th>12.25</th></tr>
<tr><td>Dettes financières non courantes</td><td>9 536 900</td><td>11 064 300</td></tr>
<tr><td>Dettes financières courantes</td><td>2 911 200</td><td>2 572 600</td></tr>
</table>
<table>
<tr><th>Ratios</th><th>12.24</th><th>12.25</th></tr>
<tr><td>Marge opérationnelle (en %)</td><td>18,28</td><td>19,59</td></tr>
<tr><td>Rentabilité financière (en %)</td><td>12,45</td><td>13,52</td></tr>
</table>
</body></html>
"""


def test_quote_and_forecast_context_is_rich_and_provider_specific():
    quote = parse_quote_context_html(QUOTE_HTML)
    forecast = parse_forward_forecasts_html(QUOTE_HTML)
    assert quote["boursorama_market_cap_meur"] == 106650
    assert quote["boursorama_estimated_yield_pct"] == 2.14
    assert quote["boursorama_estimated_per"] == 26.61
    assert quote["boursorama_pea_eligible_displayed"] is True
    assert quote["boursorama_esg_risk_score"] == 12.6
    assert forecast["boursorama_eps_est_2026"] == 6.34
    assert forecast["boursorama_eps_est_2027"] == 7.15
    assert forecast["boursorama_dividend_est_2026"] == 3.60
    assert forecast["boursorama_yield_est_2027_pct"] == 2.27
    assert forecast["boursorama_eps_est_2026"] != 6349.0


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


def test_selected_boursorama_uses_three_family_ttls_and_never_persists_html(tmp_path: Path):
    calls = []

    def fetcher(url, timeout):
        calls.append(url)
        return FakeResponse(DEEP_HTML if "chiffres-cles" in url else QUOTE_HTML)

    rows = pd.DataFrame([{"isin": "FR0000120073", "asset_class": "ACTION", "horizon": "CT", "yahoo_ticker": "AI.PA"}])
    cache = tmp_path / "boursorama_selected.json"
    now = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)
    first = collect_selected_action_context_cached(rows, cache, request_start_interval_seconds=0, fetcher=fetcher, now=now)
    call_count = len(calls)
    second = collect_selected_action_context_cached(rows, cache, request_start_interval_seconds=0, fetcher=fetcher, now=now)
    assert first.metrics["refresh_success"] == 1
    assert second.metrics["refresh_requested"] == 0
    assert len(calls) == call_count == 3
    assert any(row["field"] == "boursorama_consensus_score" for row in second.observations)
    assert any(row["field"] == "boursorama_operating_margin_pct" for row in second.observations)
    assert any(row["field"] == "boursorama_perf_1w_pct" for row in second.observations)
    assert any(row["field"] == "boursorama_dynamic_age_hours" for row in second.observations)
    assert "<html>" not in cache.read_text(encoding="utf-8")
