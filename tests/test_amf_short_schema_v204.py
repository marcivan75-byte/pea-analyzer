from __future__ import annotations

import pandas as pd

from v183.smart_money.sources.amf_short_open_data import normalize


def test_amf_current_semantic_headers_are_recognized():
    frame = pd.DataFrame([
        {
            "Detenteur de la position courte nette": "Fund A",
            "Legal Entity Identifier detenteur": "LEI123",
            "Emetteur": "Issuer SA",
            "ISIN": "FR0000000001",
            "Ratio de la position courte nette": "0,72 %",
            "Date de la position courte nette": "07/08/2026",
            "Date de debut de publication": "07/08/2026",
            "Date de fin de publication": "",
        }
    ])
    out = normalize(frame)
    assert out.loc[0, "holder"] == "Fund A"
    assert out.loc[0, "holder_lei"] == "LEI123"
    assert out.loc[0, "issuer"] == "Issuer SA"
    assert out.loc[0, "isin"] == "FR0000000001"
    assert out.loc[0, "short_position_pct"] == 0.72
    assert out.loc[0, "position_date"] == "2026-08-07"
    assert out.loc[0, "publication_start"] == "2026-08-07"


def test_amf_historical_headers_still_work():
    frame = pd.DataFrame([
        {
            "Nom du détenteur": "Fund B",
            "LEI du détenteur": "LEI456",
            "Nom de l'émetteur": "Issuer NV",
            "ISIN": "NL0000000002",
            "Position courte nette": "1.15",
            "Date de début de position": "2026-08-06",
            "Date de début de publication de la position": "2026-08-06",
        }
    ])
    out = normalize(frame)
    assert out.loc[0, "holder"] == "Fund B"
    assert out.loc[0, "issuer"] == "Issuer NV"
    assert out.loc[0, "short_position_pct"] == 1.15
    assert out.loc[0, "position_date"] == "2026-08-06"
