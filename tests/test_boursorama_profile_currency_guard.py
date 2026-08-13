from __future__ import annotations

from pathlib import Path

from v182.sources.boursorama_profile_currency_guard import sanitize_profile_market_cap_observations


def _obs(source_file: str, field: str, value) -> dict:
    return {
        "universe": "ACTION",
        "isin": "NO0003078800",
        "field": field,
        "value": value,
        "source": "Boursorama",
        "source_url": "https://www.boursorama.com/cours/societe/profil/TEST/",
        "source_file": source_file,
        "collected_at": "2026-08-13T08:00:00+00:00",
        "as_of": "2026-08-13",
        "evidence_level": "B",
        "validation_status": "ATTRIBUTED",
    }


def test_local_currency_market_cap_is_never_merged_as_eur(tmp_path: Path):
    page = tmp_path / "tgs.html"
    page.write_text(
        """
        <html><body>
          <div>Valorisation</div><div>26 541 MNOK</div>
        </body></html>
        """,
        encoding="utf-8",
    )
    observations = [
        _obs(str(page), "boursorama_market_cap_eur_m", 26541.0),
        _obs(str(page), "market_cap", 26_541_000_000.0),
        _obs(str(page), "sector_v21", "Energy"),
    ]
    safe, failures, stats = sanitize_profile_market_cap_observations(tmp_path, observations)
    assert failures == []
    fields = {row["field"]: row["value"] for row in safe}
    assert "market_cap" not in fields
    assert "boursorama_market_cap_eur_m" not in fields
    assert fields["boursorama_market_cap_reported_m"] == 26541.0
    assert fields["boursorama_market_cap_currency"] == "NOK"
    assert fields["boursorama_market_cap_reported_raw"] == "26 541 MNOK"
    assert fields["sector_v21"] == "Energy"
    assert stats["market_cap_dropped_local_currency_observations"] == 2


def test_eur_market_cap_is_retained(tmp_path: Path):
    page = tmp_path / "airbus.html"
    page.write_text(
        """
        <html><body>
          <div>Valorisation</div><div>170 064 M EUR</div>
        </body></html>
        """,
        encoding="utf-8",
    )
    observations = [
        _obs(str(page), "boursorama_market_cap_eur_m", 170064.0),
        _obs(str(page), "market_cap", 170_064_000_000.0),
    ]
    safe, failures, stats = sanitize_profile_market_cap_observations(tmp_path, observations)
    assert failures == []
    fields = {row["field"]: row["value"] for row in safe}
    assert fields["market_cap"] == 170_064_000_000.0
    assert fields["boursorama_market_cap_eur_m"] == 170064.0
    assert "boursorama_market_cap_currency" not in fields
    assert stats["market_cap_retained_eur_observations"] == 2


def test_unknown_market_cap_currency_is_not_assumed_eur(tmp_path: Path):
    page = tmp_path / "unknown.html"
    page.write_text("<html><body><div>Valorisation</div><div>12 345 M</div></body></html>", encoding="utf-8")
    observations = [_obs(str(page), "market_cap", 12_345_000_000.0)]
    safe, failures, stats = sanitize_profile_market_cap_observations(tmp_path, observations)
    assert failures == []
    fields = {row["field"]: row["value"] for row in safe}
    assert "market_cap" not in fields
    assert fields["boursorama_market_cap_reported_m"] == 12345.0
    assert "boursorama_market_cap_currency" not in fields
    assert stats["market_cap_dropped_unknown_currency_observations"] == 1


def test_local_currency_dividend_is_preserved_as_context_not_eur(tmp_path: Path):
    page = tmp_path / "norway.html"
    page.write_text(
        """
        <html><body>
          <div>Dernier dividende</div><div>8,50 NOK</div>
        </body></html>
        """,
        encoding="utf-8",
    )
    observations = [_obs(str(page), "boursorama_last_dividend_amount_eur", 8.5)]
    safe, failures, stats = sanitize_profile_market_cap_observations(tmp_path, observations)
    assert failures == []
    fields = {row["field"]: row["value"] for row in safe}
    assert "boursorama_last_dividend_amount_eur" not in fields
    assert fields["boursorama_last_dividend_amount_reported"] == 8.5
    assert fields["boursorama_last_dividend_currency"] == "NOK"
    assert fields["boursorama_last_dividend_reported_raw"] == "8,50 NOK"
    assert stats["dividend_dropped_local_currency_observations"] == 1


def test_eur_dividend_is_retained(tmp_path: Path):
    page = tmp_path / "france.html"
    page.write_text(
        """
        <html><body>
          <div>Dernier dividende</div><div>3,20 EUR</div>
        </body></html>
        """,
        encoding="utf-8",
    )
    observations = [_obs(str(page), "boursorama_last_dividend_amount_eur", 3.2)]
    safe, failures, stats = sanitize_profile_market_cap_observations(tmp_path, observations)
    assert failures == []
    fields = {row["field"]: row["value"] for row in safe}
    assert fields["boursorama_last_dividend_amount_eur"] == 3.2
    assert "boursorama_last_dividend_currency" not in fields
    assert stats["dividend_retained_eur_observations"] == 1
