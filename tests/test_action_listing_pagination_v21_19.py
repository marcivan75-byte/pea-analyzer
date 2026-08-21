from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from v182.sources import action_listing_evidence as listing


def test_action_listing_evidence_uses_paginated_table_without_detail_calls(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worklist_path = tmp_path / "worklist.csv"
    pd.DataFrame(
        [
            {
                "isin": "NO0012851874",
                "ticker": "DOFG.OL",
                "first_observed_date": "2023-06-22",
                "source_run": "32379163874",
                "initial_status": "START_AFTER_ANCHOR_UNRESOLVED",
            }
        ]
    ).to_csv(worklist_path, sep=";", index=False)

    captured: dict[str, object] = {}

    def fake_collect(start, end, timeout=20, **kwargs):
        captured.update(kwargs)
        return [
            {
                "isin": "NO0012851874",
                "name": "DOF Group",
                "symbol": "DOFG",
                "exchange": "Euronext Growth Oslo",
                "expected_date": "2023-06-22",
                "euronext_location": "Oslo",
                "euronext_source_page": f"{listing.EURONEXT_IPO_ALL}?page=12",
            }
        ], {
            "status": "SUCCESS",
            "pagination_complete": True,
        }

    monkeypatch.setattr(listing, "collect_euronext_v1_3", fake_collect)

    accepted, quarantine, metrics = listing.collect_action_listing_evidence(
        worklist_path=worklist_path,
        end=date(2026, 8, 21),
    )

    assert captured["enrich_details"] is False
    assert quarantine == []
    assert len(accepted) == 1
    assert accepted[0]["isin"] == "NO0012851874"
    assert accepted[0]["source_url"].endswith("?page=12")
    assert metrics["source_metrics"]["pagination_complete"] is True
    assert metrics["synthetic_history_created"] is False
    assert metrics["calibration_eligibility_changed"] is False
