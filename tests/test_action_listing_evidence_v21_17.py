from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from v182.audit.ohlcv_history_depth import _instrument_row
from v182.sources.action_listing_evidence import (
    apply_frozen_listing_evidence,
    load_frozen_listing_evidence,
    load_worklist,
)

WORKLIST = Path("config/V21_17_ACTION_SHORT_HISTORY_WORKLIST.csv")
FROZEN_EVIDENCE = Path("config/V21_17_ACTION_LISTING_EVIDENCE_A.csv")
QUARANTINE = Path("config/V21_17_ACTION_LISTING_QUARANTINE.csv")
EXPECTED_LOGICAL_SHA256 = "a2c446f7d82de44c6dd8218923000d93914b8b7fae03e99b4dc1f196cf4f2ba1"
EXPECTED_EVIDENCE_LOGICAL_SHA256 = "76d505d4465a417c30118ce507e7b311b9068c6190cbd2a92e09951f39cd392c"
EXPECTED_ACCEPTED_ISINS = {
    "IT0005717241",
    "FR0014018Y10",
    "IT0005713232",
    "IT0005722191",
    "IT0005720500",
    "IT0005723074",
}
EXPECTED_ARTIFACT_SHA256 = "db1e57e2219f2b879b64e50665ade350f25c5e2653e270c3182894d2bb1783a1"


def _logical_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _business_series(start: str, end: str) -> pd.Series:
    index = pd.date_range(start, end, freq="B", tz="UTC")
    return pd.Series(range(1, len(index) + 1), index=index, dtype=float)


def test_frozen_worklist_is_exact_real_v21_13_population() -> None:
    frame = load_worklist(WORKLIST)
    assert len(frame) == 157
    assert frame["isin"].nunique() == 157
    assert set(frame["source_run"]) == {"32379163874"}
    assert set(frame["initial_status"]) == {"START_AFTER_ANCHOR_UNRESOLVED"}
    assert _logical_sha256(WORKLIST) == EXPECTED_LOGICAL_SHA256


def test_frozen_accepted_evidence_is_exact_real_proof_population() -> None:
    frame = load_frozen_listing_evidence(FROZEN_EVIDENCE)
    assert len(frame) == 6
    assert set(frame["isin"]) == EXPECTED_ACCEPTED_ISINS
    assert set(frame["evidence_level"]) == {"A"}
    assert set(frame["validation_status"]) == {"EXACT_ISIN_OFFICIAL_LISTING_DATE"}
    assert set(frame["source_run"]) == {"32414686922"}
    assert set(frame["source_artifact_sha256"]) == {EXPECTED_ARTIFACT_SHA256}
    assert _logical_sha256(FROZEN_EVIDENCE) == EXPECTED_EVIDENCE_LOGICAL_SHA256


def test_frozen_quarantine_is_never_promoted_to_evidence() -> None:
    frame = pd.read_csv(QUARANTINE, sep=";", encoding="utf-8-sig", dtype=str)
    assert len(frame) == 4
    assert frame["isin"].nunique() == 4
    assert set(frame["status"]) == {"QUARANTINE"}
    assert not set(frame["isin"]).intersection(EXPECTED_ACCEPTED_ISINS)


def test_frozen_overlay_applies_exact_isin_metadata_only() -> None:
    actions = pd.DataFrame(
        [
            {"isin": "FR0014018Y10", "listing_or_launch_date": pd.NA},
            {"isin": "FR0000000000", "listing_or_launch_date": pd.NA},
        ]
    )
    metrics = apply_frozen_listing_evidence(actions, FROZEN_EVIDENCE)
    accepted = actions.loc[actions["isin"].eq("FR0014018Y10")].iloc[0]
    untouched = actions.loc[actions["isin"].eq("FR0000000000")].iloc[0]
    assert accepted["listing_or_launch_date"] == "2026-07-14"
    assert accepted["listing_or_launch_date_source"] == "EURONEXT_OFFICIAL_IPO_SHOWCASE_V21_17"
    assert accepted["listing_or_launch_date_evidence_level"] == "A"
    assert pd.isna(untouched["listing_or_launch_date"])
    assert metrics["applied"] == 1
    assert metrics["synthetic_history_created"] is False
    assert metrics["calibration_eligibility_changed"] is False


def test_frozen_overlay_conflicting_existing_date_fails_closed() -> None:
    actions = pd.DataFrame(
        [{"isin": "FR0014018Y10", "listing_or_launch_date": "2026-07-13"}]
    )
    with pytest.raises(ValueError, match="ACTION_LISTING_EVIDENCE_CONFLICT:FR0014018Y10"):
        apply_frozen_listing_evidence(actions, FROZEN_EVIDENCE)


def test_official_post_anchor_listing_explains_history_but_never_promotes_calibration() -> None:
    actions = pd.DataFrame(
        [{"isin": "FR0014018Y10", "listing_or_launch_date": pd.NA}]
    )
    apply_frozen_listing_evidence(actions, FROZEN_EVIDENCE)
    listing_date = actions.iloc[0]["listing_or_launch_date"]
    row = _instrument_row(
        "ACTION",
        "FR0014018Y10",
        "ALLSF.PA",
        _business_series("2026-07-14", "2026-08-20"),
        primary_start=pd.Timestamp("2023-01-01", tz="UTC"),
        as_of=pd.Timestamp("2026-08-20", tz="UTC"),
        listing_or_launch_date=listing_date,
    )
    assert row["primary_status"] == "POST_ANCHOR_INCEPTION_CONFIRMED"
    assert row["primary_calibration_eligible"] is False
    assert row["synthetic_pre_inception_history"] is False
    assert row["inception_evidence_changes_calibration_eligibility"] is False
