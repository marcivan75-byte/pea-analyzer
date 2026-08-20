from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.audit.ohlcv_history_depth import _instrument_row
from v182.sources.etf_inception_data import (
    _issuer_observations_from_text,
    _justetf_observations_from_text,
)


def _close(start: str, end: str) -> pd.Series:
    index = pd.date_range(start, end, freq="B", tz="UTC")
    return pd.Series(range(1, len(index) + 1), index=index, dtype=float)


def test_amundi_exact_isin_extracts_share_class_date_and_keeps_first_nav_context_only():
    text = """
    Amundi STOXX Europe 600 Banks UCITS ETF Acc
    Code ISIN LU1834983477
    Date de création de la classe 21/03/2024
    Date de la première VL 24/08/2006
    """
    rows = _issuer_observations_from_text(
        "LU1834983477",
        text,
        source="Amundi official monthly factsheet",
        source_url="https://issuer.example/factsheet.pdf",
        as_of="2026-07-31",
    )
    values = {row["field"]: row["value"] for row in rows}
    assert values == {
        "share_class_inception_date": "2024-03-21",
        "reported_first_nav_date": "2006-08-24",
    }
    assert {row["evidence_level"] for row in rows} == {"A"}
    assert {row["validation_status"] for row in rows} == {"EXACT_ISIN_SOURCE_MATCH"}


def test_justetf_exact_isin_extracts_french_launch_date_as_evidence_b():
    text = """
    Amundi MSCI World Swap UCITS ETF EUR Dist
    ISIN LU2655993207
    Date de lancement / première cotation 15 mars 2024
    """
    rows = _justetf_observations_from_text(
        "LU2655993207",
        text,
        source_url="https://www.justetf.com/fr/etf-profile.html?isin=LU2655993207",
        as_of="2026-08-20",
    )
    assert len(rows) == 1
    assert rows[0]["field"] == "listing_or_launch_date"
    assert rows[0]["value"] == "2024-03-15"
    assert rows[0]["evidence_level"] == "B"


def test_inception_parser_fails_closed_on_mismatched_isin():
    rows = _issuer_observations_from_text(
        "LU1834983477",
        "Code ISIN LU0000000000 Date de création de la classe 21/03/2024",
        source="issuer",
        source_url="https://issuer.example",
        as_of="2026-07-31",
    )
    assert rows == []


def test_post_anchor_share_class_is_explained_but_not_promoted_to_full_calibration():
    row = _instrument_row(
        "ETF",
        "LU1834983477",
        "BNK.PA",
        _close("2024-04-17", "2026-08-20"),
        primary_start=pd.Timestamp("2023-01-01", tz="UTC"),
        as_of=pd.Timestamp("2026-08-20", tz="UTC"),
        share_class_inception_date="2024-03-21",
        reported_first_nav_date="2006-08-24",
    )
    assert row["primary_status"] == "POST_ANCHOR_INCEPTION_CONFIRMED"
    assert row["launch_date"] == "2024-03-21"
    assert row["launch_date_source"] == "share_class_inception_date"
    assert row["reported_first_nav_authoritative_for_history_start"] is False
    assert row["primary_calibration_eligible"] is False
    assert row["stress_library_eligible"] is False
    assert row["synthetic_pre_inception_history"] is False


def test_pre_anchor_inception_with_late_yahoo_start_is_data_gap_not_new_fund():
    row = _instrument_row(
        "ETF",
        "FR0010655746",
        "CS1.PA",
        _close("2024-04-05", "2026-08-20"),
        primary_start=pd.Timestamp("2023-01-01", tz="UTC"),
        as_of=pd.Timestamp("2026-08-20", tz="UTC"),
        share_class_inception_date="2008-09-16",
    )
    assert row["primary_status"] == "PRE_ANCHOR_INCEPTION_HISTORY_GAP_CONFIRMED"
    assert row["primary_calibration_eligible"] is False
    assert "PROVIDER_OR_LISTING_HISTORY_GAP" in row["short_history_reason"]


def test_reported_first_nav_alone_never_resolves_short_history():
    row = _instrument_row(
        "ETF",
        "LU1834983477",
        "BNK.PA",
        _close("2024-04-17", "2026-08-20"),
        primary_start=pd.Timestamp("2023-01-01", tz="UTC"),
        as_of=pd.Timestamp("2026-08-20", tz="UTC"),
        reported_first_nav_date="2006-08-24",
    )
    assert row["primary_status"] == "START_AFTER_ANCHOR_UNRESOLVED"
    assert row["launch_date"] is None


def test_secondary_listing_date_can_explain_short_history_without_promotion():
    row = _instrument_row(
        "ETF",
        "LU2655993207",
        "EWLD.PA",
        _close("2024-03-15", "2026-08-20"),
        primary_start=pd.Timestamp("2023-01-01", tz="UTC"),
        as_of=pd.Timestamp("2026-08-20", tz="UTC"),
        listing_or_launch_date="2024-03-15",
    )
    assert row["primary_status"] == "POST_ANCHOR_INCEPTION_CONFIRMED"
    assert row["launch_date_source"] == "listing_or_launch_date"
    assert row["primary_calibration_eligible"] is False


def test_inception_after_observed_market_history_is_conflict_and_blocks():
    row = _instrument_row(
        "ETF",
        "XX0000000001",
        "TEST.PA",
        _close("2024-01-02", "2026-08-20"),
        primary_start=pd.Timestamp("2023-01-01", tz="UTC"),
        as_of=pd.Timestamp("2026-08-20", tz="UTC"),
        share_class_inception_date="2025-01-01",
    )
    assert row["primary_status"] == "INCEPTION_EVIDENCE_CONFLICT_AFTER_FIRST_OBSERVATION"
    assert row["primary_calibration_eligible"] is False
    assert row["short_history_reason"] == "INCEPTION_EVIDENCE_CONFLICT_BLOCK_DATA"


def test_state_config_persists_static_inception_fields_without_changing_model_authority():
    config = json.loads(Path("config/ETF_STRUCTURE_STATE_V21_15.json").read_text(encoding="utf-8"))
    fields = config["fields"]
    governance = config["governance"]
    assert fields["share_class_inception_date"]["max_age_days"] == 36525
    assert fields["listing_or_launch_date"]["max_age_days"] == 36525
    assert fields["reported_first_nav_date"]["max_age_days"] == 36525
    assert governance["reported_first_nav_context_only"] is True
    assert governance["inception_evidence_changes_calibration_eligibility"] is False
    assert governance["synthetic_pre_inception_history"] is False
    assert governance["weights_changed"] is False
    assert governance["thresholds_changed"] is False
