from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from v182.sources.boursorama_selected_etf import (
    collect_selected_etf_context_cached,
    parse_etf_risk_html,
    parse_etf_sheet_html,
)


ETF_SHEET = """
<html><body>
Éligibilité PEA
Ouverture théorique 6,8820 ouverture 6,8700 clôture veille 6,8510 + haut 6,9010 + bas 6,8420 volume 325 100
Actif net (EUR) 1,66B / Société de gestion Amundi Asset Management Gérants Equipe indicielle
Catégorie morningstar Actions Europe Grandes Cap. Mixte ouverture
Classe d'actifs Actions Zone géographique Europe Dividende
Affectation des résultats Capitalisation Réplication Synthétique Frais de gestion maximum 0,25 %
</body></html>
"""

ETF_RISK = """
<html><body>
Éligibilité PEA Classe d'actifs Actions Zone géographique Europe Affectation des résultats Capitalisation Réplication Synthétique Frais de gestion maximum 0,25 %
<table>
<tr><th>Volatilité 1 an</th><th>Alpha 1 an</th><th>R² 1 an</th><th>Beta 1 an</th></tr>
<tr><td>15,20</td><td>1,10</td><td>0,96</td><td>1,02</td></tr>
</table>
</body></html>
"""


def test_etf_sheet_and_risk_parser_preserve_boursorama_semantics():
    sheet = parse_etf_sheet_html(ETF_SHEET)
    risk = parse_etf_risk_html(ETF_RISK)
    assert sheet["boursorama_etf_pea_eligible_displayed"] is True
    assert sheet["boursorama_etf_theoretical_open"] == 6.882
    assert sheet["boursorama_etf_aum_eur_m"] == 1660.0
    assert sheet["boursorama_etf_management_fee_pct"] == 0.25
    assert "Synthétique" in sheet["boursorama_etf_replication"]
    assert risk["boursorama_etf_volatility_1y_pct"] == 15.2
    assert risk["boursorama_etf_alpha_1y"] == 1.1
    assert risk["boursorama_etf_r2_1y"] == 0.96
    assert risk["boursorama_etf_beta_1y"] == 1.02


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


def test_selected_etf_cache_uses_two_ttls_and_no_raw_html(tmp_path: Path):
    calls = []

    def fetcher(url, timeout):
        calls.append(url)
        return FakeResponse(ETF_RISK if "performances-risques" in url else ETF_SHEET)

    rows = pd.DataFrame(
        [{"isin": "FR001400U5Q4", "asset_class": "ETF", "horizon": "MT", "yahoo_ticker": "WPEA.PA"}]
    )
    cache = tmp_path / "boursorama_etf.json"
    now = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)
    first = collect_selected_etf_context_cached(
        rows,
        cache,
        request_start_interval_seconds=0,
        fetcher=fetcher,
        now=now,
    )
    first_calls = len(calls)
    second = collect_selected_etf_context_cached(
        rows,
        cache,
        request_start_interval_seconds=0,
        fetcher=fetcher,
        now=now,
    )
    assert first.metrics["refresh_success"] == 1
    assert second.metrics["refresh_requested"] == 0
    assert len(calls) == first_calls == 2
    assert any(row["field"] == "boursorama_etf_aum_eur_m" for row in second.observations)
    assert any(row["field"] == "boursorama_etf_beta_1y" for row in second.observations)
    assert all(len(row["page_sha256"]) == 64 for row in second.observations)
    assert "<html>" not in cache.read_text(encoding="utf-8")


def test_etf_cache_is_invalidated_when_boursorama_code_changes(tmp_path: Path):
    cache = tmp_path / "boursorama_etf.json"
    now = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)

    def fetcher(url, timeout):
        return FakeResponse(ETF_RISK if "performances-risques" in url else ETF_SHEET)

    first = pd.DataFrame(
        [{"isin": "LU0000000001", "asset_class": "ETF", "horizon": "MT", "yahoo_ticker": "WPEA.PA"}]
    )
    changed = first.assign(yahoo_ticker="ESE.PA")
    collect_selected_etf_context_cached(
        first, cache, request_start_interval_seconds=0, fetcher=fetcher, now=now
    )
    result = collect_selected_etf_context_cached(
        changed, cache, request_start_interval_seconds=0, fetcher=fetcher, now=now
    )
    assert result.metrics["refresh_requested"] == 1
    assert any(row["reason"] == "CACHE_IDENTITY_CHANGED_REFRESH_REQUIRED" for row in result.failures)
    assert all("/1rTESE/" in row["source_url"] for row in result.observations)
