from pathlib import Path

from v182.audit.canonical_universe import IDENTITY_ONLY_STATUS, filter_actions
from v182.audit.master_data import audit_frame
from v182.io.frames import load_master
from v182.mapping.action_isin_resolver import apply_identity_overlay
from v182.mapping.identity_overlay_store import (
    EXPECTED_RAW_SHA256,
    decode_identity_overlay,
    materialize_identity_overlay,
)

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_identity_overlay_has_governed_counts_and_digest() -> None:
    overlay = decode_identity_overlay(ROOT)
    assert EXPECTED_RAW_SHA256 == "0dfecb6a4014c4c77b1b5e17379ebf202e427bc50f190b86a0d46967dceebd20"
    assert len(overlay) == 399
    assert overlay["isin"].nunique() == 399
    assert int(overlay["identity_resolution_status"].eq("VALIDATED").sum()) == 360
    assert int(overlay["name"].notna().sum()) == 399
    assert int(overlay["yahoo_ticker"].notna().sum()) == 360


def test_governed_actions_have_1790_market_data_eligible_rows() -> None:
    legacy = load_master(ROOT / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
    canonical = filter_actions(legacy, ROOT / "config" / "V21_3_ACTION_UNIVERSE_1829_ISINS.parts")
    overlay_path = materialize_identity_overlay(ROOT)
    assert overlay_path is not None
    governed, overlay_audit = apply_identity_overlay(canonical.included, overlay_path)
    audit = audit_frame(governed, "ACTION")
    assert overlay_audit["fully_hydrated"] == 360
    assert audit.summary["rows"] == 1829
    assert audit.summary["identity_only_rows"] == 39
    assert audit.summary["market_data_eligible_rows"] == 1790
    assert audit.summary["identity_coverage_pct"]["name"] == 100.0
    assert audit.summary["identity_coverage_pct"]["yahoo_ticker"] == 97.87
    assert audit.summary["issue_counts"]["FATAL"] == 0
    remaining = governed[governed["canonical_seed_status"].astype(str).eq(IDENTITY_ONLY_STATUS)]
    assert len(remaining) == 39
    assert remaining["name"].notna().all()
    assert remaining["yahoo_ticker"].isna().all()
