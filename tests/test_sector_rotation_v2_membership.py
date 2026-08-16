from pathlib import Path

import pandas as pd

from v182.features.sector_rotation_v2_membership import append_membership_history, build_membership_snapshot


def test_membership_freezes_only_scored_sectors_and_prefers_isin(tmp_path: Path):
    actions = pd.DataFrame(
        {
            "isin": ["FR1", "FR2", "FR3", "FR4"],
            "yahoo_ticker": ["A.PA", "B.PA", "C.PA", "D.PA"],
            "name": ["A", "B", "C", "D"],
            "sector_yf": ["Technology", "Technology", "Energy", "Energy"],
        }
    )
    snapshot = build_membership_snapshot(
        actions,
        ["Technology"],
        as_of="2026-09-01",
        model_version="V2",
    )
    assert snapshot["sector"].unique().tolist() == ["Technology"]
    assert snapshot["instrument_key"].tolist() == ["FR1", "FR2"]

    path = tmp_path / "membership.csv"
    append_membership_history(snapshot, path)
    append_membership_history(snapshot, path)
    stored = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    assert len(stored) == 2


def test_membership_falls_back_to_ticker_then_name():
    actions = pd.DataFrame(
        {
            "isin": [None, None],
            "yahoo_ticker": ["A.PA", None],
            "name": ["A", "B"],
            "sector": ["Technology", "Technology"],
        }
    )
    snapshot = build_membership_snapshot(
        actions,
        ["Technology"],
        as_of="2026-09-01",
        model_version="V2",
    )
    assert snapshot["instrument_key"].tolist() == ["A.PA", "B"]
