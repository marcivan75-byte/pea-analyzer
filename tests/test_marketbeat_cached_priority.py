from pathlib import Path

import pandas as pd

from v182.decision.marketbeat_overlay_runner import _select_marketbeat_rows


def test_validated_mapping_is_prioritized_inside_committee_candidate_pool(tmp_path: Path):
    actions = pd.DataFrame([
        {
            "isin": "TOP",
            "name": "TOP SCORE",
            "yahoo_ticker": "TOP.PA",
            "score_brut": "90",
            "n_analysts": "5",
            "comite_status": "NONE",
        },
        {
            "isin": "FR0000120578",
            "name": "SANOFI",
            "yahoo_ticker": "SAN.PA",
            "score_brut": "80",
            "n_analysts": "29",
            "comite_status": "NONE",
        },
        {
            "isin": "OTHER",
            "name": "OTHER",
            "yahoo_ticker": "OTH.PA",
            "score_brut": "85",
            "n_analysts": "10",
            "comite_status": "NONE",
        },
    ])
    mapping = tmp_path / "marketbeat.csv"
    mapping.write_text(
        "isin;status\nFR0000120578;RESOLVED\n",
        encoding="utf-8",
    )

    selected = _select_marketbeat_rows(
        actions,
        2,
        mapping_path=mapping,
        candidate_pool=100,
    )
    assert selected[0]["isin"] == "FR0000120578"
    assert selected[1]["isin"] == "TOP"


def test_validated_mapping_outside_candidate_pool_is_not_promoted(tmp_path: Path):
    actions = pd.DataFrame([
        {
            "isin": f"X{i}",
            "name": f"NAME {i}",
            "yahoo_ticker": f"X{i}.PA",
            "score_brut": str(100 - i),
            "n_analysts": "10",
            "comite_status": "NONE",
        }
        for i in range(10)
    ])
    mapping = tmp_path / "marketbeat.csv"
    mapping.write_text("isin;status\nX9;RESOLVED\n", encoding="utf-8")

    selected = _select_marketbeat_rows(
        actions,
        2,
        mapping_path=mapping,
        candidate_pool=5,
    )
    assert "X9" not in {row["isin"] for row in selected}
