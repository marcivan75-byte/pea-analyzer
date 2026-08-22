from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from v182.sources.boursorama_public import (
    action_urls,
    boursorama_code,
    collect_action_snapshots_cached,
    parse_action_consensus_html,
    parse_action_key_figures_html,
)

CONSENSUS_HTML = """
<html><body><table>
<tr><th>Consensus</th><th>3 mois</th><th>2 mois</th><th>1 mois</th><th>7 jours</th><th>22/08/2026</th></tr>
<tr><td>Acheter</td><td>13</td><td>13</td><td>13</td><td>15</td><td>15</td></tr>
<tr><td>Renforcer</td><td>5</td><td>5</td><td>5</td><td>4</td><td>4</td></tr>
<tr><td>Conserver</td><td>3</td><td>3</td><td>3</td><td>3</td><td>4</td></tr>
<tr><td>Alléger</td><td>1</td><td>1</td><td>1</td><td>0</td><td>0</td></tr>
<tr><td>Vendre</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>Note médiane</td><td>1,64</td><td>1,64</td><td>1,64</td><td>1,45</td><td>1,52</td></tr>
<tr><td>Objectif de cours médian</td><td>197,50</td><td>197,00</td><td>181,59</td><td>193,12</td><td>196,09</td></tr>
<tr><td>Potentiel</td><td>10,0%</td><td>10,0%</td><td>8,0%</td><td>12,0%</td><td>14,38%</td></tr>
</table></body></html>
"""

KEY_HTML = """
<html><body>
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


def test_deterministic_codes_cover_verified_european_markets_without_search():
    assert boursorama_code({"yahoo_ticker": "AI.PA"}, "ACTION") == "1rPAI"
    assert boursorama_code({"yahoo_ticker": "ASML.AS"}, "ACTION") == "1rAASML"
    assert boursorama_code({"yahoo_ticker": "EDP.LS"}, "ACTION") == "1rLEDP"
    assert boursorama_code({"yahoo_ticker": "ABI.BR"}, "ACTION") == "FF11-ABI"
    assert boursorama_code({"yahoo_ticker": "SAN.MC"}, "ACTION") == "FF55-SAN"
    assert boursorama_code({"yahoo_ticker": "ENI.MI"}, "ACTION") == "1gENI"
    assert boursorama_code({"yahoo_ticker": "SIE.DE"}, "ACTION") == "1zSIE"
    assert boursorama_code({"yahoo_ticker": "WPEA.PA"}, "ETF") == "1rTWPEA"
    assert boursorama_code({"yahoo_ticker": "NOVN.SW"}, "ACTION") is None
    assert boursorama_code({"yahoo_ticker": "VOW3.DE"}, "ETF") is None


def test_explicit_validated_override_still_has_priority():
    assert boursorama_code({"boursorama_code": "STATIC-VERIFIED", "yahoo_ticker": "NOVN.SW"}, "ACTION") == "STATIC-VERIFIED"


def test_consensus_parser_reuses_finnhub_weight_semantics_and_keeps_median_target_separate():
    fields = parse_action_consensus_html(CONSENSUS_HTML)
    expected_score = (15 * 5 + 4 * 4 + 4 * 3) / 23
    previous_score = (13 * 5 + 5 * 4 + 3 * 3 + 1 * 2) / 22
    assert fields["boursorama_n_analysts"] == 23
    assert fields["boursorama_buy_n"] == 19
    assert fields["boursorama_hold_n"] == 4
    assert fields["boursorama_sell_n"] == 0
    assert fields["boursorama_consensus_score"] == round(expected_score, 4)
    assert fields["boursorama_consensus_delta_4w"] == round((expected_score - previous_score) * 20, 4)
    assert fields["boursorama_target_median"] == 196.09
    assert fields["boursorama_target_upside_pct"] == 14.38
    assert "target_price" not in fields


def test_key_figures_parser_keeps_provider_specific_semantics():
    fields = parse_action_key_figures_html(KEY_HTML)
    assert fields["boursorama_operating_margin_pct"] == 19.59
    assert fields["boursorama_roe_pct"] == 13.52
    assert fields["boursorama_total_financial_debt_thousands"] == 13_636_900
    assert fields["boursorama_revenue_thousands"] == 26_940_200
    assert fields["boursorama_net_income_group_thousands"] == 3_517_900
    assert "free_cash_flow" not in fields
    assert "beta" not in fields


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


def test_cache_persists_normalized_values_and_hash_not_raw_html(tmp_path: Path):
    rows = pd.DataFrame([{"isin": "FR0000120073", "yahoo_ticker": "AI.PA"}])
    calls = []

    def fetcher(url, timeout):
        calls.append((url, timeout))
        return FakeResponse(CONSENSUS_HTML)

    cache = tmp_path / "boursorama.json"
    now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
    first = collect_action_snapshots_cached(
        rows, cache, refresh_budget=10, request_start_interval_seconds=0, fetcher=fetcher, now=now
    )
    second = collect_action_snapshots_cached(
        rows, cache, refresh_budget=10, request_start_interval_seconds=0, fetcher=fetcher, now=now
    )
    raw = cache.read_text(encoding="utf-8")
    assert first.metrics["live_refresh_success"] == 1
    assert second.metrics["live_refresh_requested"] == 0
    assert len(calls) == 1
    assert "<html>" not in raw
    assert "page_sha256" in raw
    assert action_urls("1rPAI")["consensus"] in raw
    assert any(row["field"] == "boursorama_consensus_score" for row in second.observations)
    assert all(row["validation_status"] == "SHADOW_ATTRIBUTED" for row in second.observations)
