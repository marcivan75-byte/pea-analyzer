from pathlib import Path

import pandas as pd

from v182.decision.marketbeat_overlay import _select_marketbeat_rows


def test_validated_mapping_is_prioritized_under_quota(tmp_path: Path):
    actions = pd.DataFrame([
        {
            "isin": "TOP",
            "name": "TOP SCORE",
            "yahoo_ticker": "TOP.PA",
            "score_brut": "90",
            "n_analysts": "50",
            "comite_status": "COMMITTEE",
        },
        {
            "isin": "FR0000120578",
            "name": "SANOFI",
            "yahoo_ticker": "SAN.PA",
            "score_brut": "80",
            "n_analysts": "29",
            "comite_status": "COMMITTEE",
        },
        {
            "isin": "OTHER",
            "name": "OTHER",
            "yahoo_ticker": "OTH.PA",
            "score_brut": "85",
            "n_analysts": "40",
            "comite_status": "COMMITTEE",
        },
    ])
    mapping = tmp_path / "marketbeat.csv"
    mapping.write_text(
        "isin;status\nFR0000120578;RESOLVED\n",
        encoding="utf-8",
    )

    selected = _select_marketbeat_rows(actions, 2, mapping_path=mapping)

    assert [row["isin"] for row in selected] == ["FR0000120578", "TOP"]


def test_unresolved_mapping_is_not_prioritized(tmp_path: Path):
    actions = pd.DataFrame([
        {
            "isin": "TOP",
            "name": "TOP SCORE",
            "yahoo_ticker": "TOP.PA",
            "score_brut": "90",
            "n_analysts": "50",
            "comite_status": "COMMITTEE",
        },
        {
            "isin": "FR0000120578",
            "name": "SANOFI",
            "yahoo_ticker": "SAN.PA",
            "score_brut": "80",
            "n_analysts": "29",
            "comite_status": "COMMITTEE",
        },
    ])
    mapping = tmp_path / "marketbeat.csv"
    mapping.write_text(
        "isin;status\nFR0000120578;UNRESOLVED\n",
        encoding="utf-8",
    )

    selected = _select_marketbeat_rows(actions, 1, mapping_path=mapping)

    assert selected[0]["isin"] == "TOP"
