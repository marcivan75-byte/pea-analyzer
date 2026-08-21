from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from v182.audit.ohlcv_history_depth import _instrument_row
from v182.sources import action_listing_evidence as listing

EVIDENCE = Path("config/V21_19_ACTION_LISTING_EVIDENCE_A.csv.gz")
META = Path("config/V21_19_ACTION_LISTING_EVIDENCE_A.meta.json")
QUARANTINE = Path("config/V21_19_ACTION_LISTING_QUARANTINE.csv")
UNRESOLVED = Path("config/V21_19_ACTION_SHORT_HISTORY_UNRESOLVED.csv")
EXPECTED_RUN = "32485729404"
EXPECTED_ARTIFACT_SHA256 = "86a3185b10877388f3d03fb0b169271d6124db8e725d743deb56b63e1a98a2de"


def _business_series(start: str, end: str) -> pd.Series:
    index = pd.date_range(start, end, freq="B", tz="UTC")
    return pd.Series(range(1, len(index) + 1), index=index, dtype=float)


def test_v21_19_frozen_evidence_matches_real_proof_manifest() -> None:
    frame = listing.load_v21_19_listing_evidence(EVIDENCE, META)
    assert len(frame) == 126
    assert frame["isin"].nunique() == 126
    assert set(frame["evidence_level"]) == {"A"}
    assert set(frame["validation_status"]) == {"EXACT_ISIN_OFFICIAL_LISTING_DATE"}
    assert set(frame["source_run"]) == {EXPECTED_RUN}
    assert set(frame["source_artifact_sha256"]) == {EXPECTED_ARTIFACT_SHA256}
    assert set(frame["source_url"].str.startswith("https://live.euronext.com/")) == {True}
    assert "FR001400F2Z1" in set(frame["isin"])
    assert "NO0012851874" in set(frame["isin"])


def test_v21_17_and_v21_19_frozen_evidence_are_non_overlapping() -> None:
    legacy = listing.load_frozen_listing_evidence(listing.DEFAULT_FROZEN_EVIDENCE)
    current = listing.load_v21_19_listing_evidence()
    combined = listing.load_all_frozen_listing_evidence()
    assert len(legacy) == 6
    assert len(current) == 126
    assert len(combined) == 132
    assert combined["isin"].nunique() == 132
    assert not set(legacy["isin"]).intersection(set(current["isin"]))


def test_v21_19_quarantine_and_unresolved_are_frozen_and_never_evidence() -> None:
    evidence = listing.load_v21_19_listing_evidence()
    quarantine = pd.read_csv(QUARANTINE, sep=";", dtype=str)
    unresolved = pd.read_csv(UNRESOLVED, sep=";", dtype=str)
    assert len(quarantine) == 10
    assert quarantine["isin"].nunique() == 10
    assert set(quarantine["status"]) == {"QUARANTINE"}
    assert set(quarantine["source_run"]) == {EXPECTED_RUN}
    assert set(quarantine["source_artifact_sha256"]) == {EXPECTED_ARTIFACT_SHA256}
    assert len(unresolved) == 15
    assert unresolved["isin"].nunique() == 15
    assert set(unresolved["status"]) == {"START_AFTER_ANCHOR_UNRESOLVED"}
    assert set(unresolved["source_run"]) == {EXPECTED_RUN}
    assert not set(evidence["isin"]).intersection(set(quarantine["isin"]))
    assert not set(evidence["isin"]).intersection(set(unresolved["isin"]))


def test_default_overlay_applies_both_frozen_generations_with_attributed_sources() -> None:
    actions = pd.DataFrame(
        [
            {"isin": "FR0014018Y10", "listing_or_launch_date": pd.NA},
            {"isin": "FR001400F2Z1", "listing_or_launch_date": pd.NA},
        ]
    )
    metrics = listing.apply_frozen_listing_evidence(actions)
    legacy = actions.loc[actions["isin"].eq("FR0014018Y10")].iloc[0]
    current = actions.loc[actions["isin"].eq("FR001400F2Z1")].iloc[0]
    assert legacy["listing_or_launch_date_source"] == "EURONEXT_OFFICIAL_IPO_SHOWCASE_V21_17"
    assert current["listing_or_launch_date_source"] == "EURONEXT_OFFICIAL_IPO_SHOWCASE_V21_19"
    assert current["listing_or_launch_date"] == "2023-02-13"
    assert metrics["evidence_rows"] == 132
    assert metrics["applied"] == 2
    assert metrics["synthetic_history_created"] is False
    assert metrics["calibration_eligibility_changed"] is False


def test_cross_version_duplicate_isin_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    duplicate = pd.DataFrame(
        [
            {
                "isin": "FR0000000001",
                "ticker": "ABC.PA",
                "official_listing_date": "2024-01-02",
                "first_observed_date": "2024-01-02",
                "source_name": "EURONEXT_OFFICIAL_IPO_SHOWCASE",
                "source_url": "https://live.euronext.com/en/ipo-showcase/all",
                "evidence_level": "A",
                "validation_status": "EXACT_ISIN_OFFICIAL_LISTING_DATE",
                "source_run": "RUN",
                "source_artifact_sha256": "a" * 64,
            }
        ]
    )
    monkeypatch.setattr(listing, "load_frozen_listing_evidence", lambda path=listing.DEFAULT_FROZEN_EVIDENCE: duplicate.copy())
    monkeypatch.setattr(listing, "load_v21_19_listing_evidence", lambda *args, **kwargs: duplicate.copy())
    with pytest.raises(ValueError, match="ACTION_LISTING_EVIDENCE_CROSS_VERSION_DUPLICATE"):
        listing.load_all_frozen_listing_evidence()


def test_post_anchor_official_listing_still_never_promotes_full_window_calibration() -> None:
    actions = pd.DataFrame(
        [{"isin": "FR001400F2Z1", "listing_or_launch_date": pd.NA}]
    )
    listing.apply_frozen_listing_evidence(actions)
    row = _instrument_row(
        "ACTION",
        "FR001400F2Z1",
        "ALLPL.PA",
        _business_series("2023-02-13", "2026-08-20"),
        primary_start=pd.Timestamp("2023-01-01", tz="UTC"),
        as_of=pd.Timestamp("2026-08-20", tz="UTC"),
        listing_or_launch_date=actions.iloc[0]["listing_or_launch_date"],
    )
    assert row["primary_status"] == "POST_ANCHOR_INCEPTION_CONFIRMED"
    assert row["primary_calibration_eligible"] is False
    assert row["synthetic_pre_inception_history"] is False
    assert row["inception_evidence_changes_calibration_eligibility"] is False
