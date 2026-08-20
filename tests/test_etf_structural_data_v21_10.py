from __future__ import annotations

from datetime import date

from v182.reporting.etf_structure_refresh import _governed_structural_observations
from v182.sources.etf_structural_data import (
    FUND_ASSET_LABELS,
    SHARE_CLASS_ASSET_LABELS,
    _eur_m_after_label,
    _first_pct,
    _localized_number,
    _month_ends,
    _observations_from_text,
)


def test_localized_numbers_cover_french_and_english_factsheets():
    assert _localized_number("4 466,14") == 4466.14
    assert _localized_number("2,372,180,132") == 2372180132.0
    assert _localized_number("1,092") == 1092.0
    assert _localized_number("0,050", percent=True) == 0.05
    assert _localized_number("0.25", percent=True) == 0.25


def test_amundi_official_text_extracts_ter_and_explicit_eur_fund_assets():
    text = """
    Amundi CAC 40 UCITS ETF Acc
    Date de VL et d'actif géré 30/06/2026
    Actif géré 4 466,14 ( millions EUR )
    Frais de gestion et autres coûts administratifs ou d’exploitation * 0,25%
    Code ISIN C : FR0013380607
    """
    rows = _observations_from_text(
        "FR0013380607",
        text,
        source="Amundi official monthly factsheet",
        source_url="https://issuer.example/factsheet.pdf",
        evidence="A",
        fallback_as_of="2026-06-30",
    )
    values = {row["field"]: row["value"] for row in rows}
    assert values["ter_pct"] == 0.25
    assert values["fund_total_assets_eur_m"] == 4466.14
    assert {row["evidence_level"] for row in rows} == {"A"}
    assert {row["as_of"] for row in rows} == {"2026-06-30"}


def test_hsbc_official_text_extracts_absolute_eur_fund_size():
    text = """
    HSBC EURO STOXX 50 UCITS ETF
    Fund size EUR 2,372,180,132
    Ongoing charge figure 0.050%
    ISIN IE00B4K6B022
    Source: HSBC Asset Management, data as at 31 May 2026
    """
    rows = _observations_from_text(
        "IE00B4K6B022",
        text,
        source="HSBC official factsheet",
        source_url="https://issuer.example/factsheet.pdf",
        evidence="A",
        fallback_as_of="2026-06-01",
    )
    values = {row["field"]: row["value"] for row in rows}
    assert values["ter_pct"] == 0.05
    assert values["fund_total_assets_eur_m"] == 2372.180132


def test_state_street_and_vanguard_scale_rules_are_explicit_eur_only():
    spdr = "ISIN IE00B910VR50 Assets Under Management €366.20 M TER 0.08%"
    assert _first_pct(spdr) == 0.08
    assert _eur_m_after_label(spdr, FUND_ASSET_LABELS) == 366.2

    vanguard = "ISIN IE00BKX55S42 Share Class Assets' €3.31 B Total Assets €4.76 B"
    assert _eur_m_after_label(vanguard, SHARE_CLASS_ASSET_LABELS) == 3310.0
    assert _eur_m_after_label(vanguard, FUND_ASSET_LABELS) == 4760.0

    usd = "ISIN IE00BKX55S42 Total Assets US$4.76 B"
    assert _eur_m_after_label(usd, FUND_ASSET_LABELS) is None


def test_justetf_exact_isin_profile_can_be_evidence_b_without_fx_guessing():
    text = """
    Amundi CAC 40 UCITS ETF Acc
    ISIN FR0013380607
    TER 0.25% p.a.
    Fund size EUR 1,092 m
    """
    rows = _observations_from_text(
        "FR0013380607",
        text,
        source="justETF exact-ISIN profile",
        source_url="https://www.justetf.com/en-be/etf-profile.html?isin=FR0013380607",
        evidence="B",
        fallback_as_of="2026-08-20",
    )
    values = {row["field"]: row["value"] for row in rows}
    assert values == {"ter_pct": 0.25, "fund_total_assets_eur_m": 1092.0}
    assert {row["evidence_level"] for row in rows} == {"B"}


def test_exact_isin_collector_proof_maps_to_existing_governed_merge_status():
    raw=[{
        "universe":"ETF",
        "isin":"FR0013380607",
        "field":"ter_pct",
        "value":0.25,
        "source":"issuer",
        "source_url":"https://issuer.example/factsheet.pdf",
        "evidence_level":"A",
        "as_of":"2026-06-30",
        "validation_status":"EXACT_ISIN_SOURCE_MATCH",
    }]
    rows=_governed_structural_observations(raw)
    assert rows[0]["validation_status"] == "ISIN_MATCHED"
    assert rows[0]["identity_validation_detail"] == "EXACT_ISIN_SOURCE_MATCH"
    assert raw[0]["validation_status"] == "EXACT_ISIN_SOURCE_MATCH"


def test_unexpected_structural_validation_status_remains_fail_closed():
    raw=[{"validation_status":"UNEXPECTED_STATUS","isin":"FR0013380607","field":"ter_pct","value":0.25}]
    rows=_governed_structural_observations(raw)
    assert rows[0]["validation_status"] == "UNEXPECTED_STATUS"
    assert "identity_validation_detail" not in rows[0]


def test_mismatched_isin_is_fail_closed():
    rows = _observations_from_text(
        "FR0013380607",
        "ISIN FR0012345678 TER 0.20% Fund size EUR 100 m",
        source="secondary",
        source_url="https://example.test",
        evidence="B",
        fallback_as_of="2026-08-20",
    )
    assert rows == []


def test_recent_month_end_candidates_are_deterministic():
    assert _month_ends(date(2026, 8, 20), 4) == ["20260731", "20260630", "20260531", "20260430"]
