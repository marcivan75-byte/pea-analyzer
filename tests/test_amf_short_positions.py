from __future__ import annotations

from datetime import datetime, timezone
import pandas as pd

from v182.sources.amf_short_positions import parse_amf_short_positions


def _fields(observations: list[dict]) -> dict[str, object]:
    return {row["field"]:row["value"] for row in observations}


def test_amf_aggregates_one_latest_open_publication_per_holder_and_never_calls_it_true_current_interest():
    actions=pd.DataFrame([
        {"isin":"FR0000120073"},
        {"isin":"FR0000120271"},
    ])
    source=pd.DataFrame([
        {"ISIN":"FR0000120073","Position courte nette":"0,40%","Date de début de position":"12/08/2026","Date de début de publication":"13/08/2026","Date de fin de publication":"","Nom du détenteur":"Fund A"},
        {"ISIN":"FR0000120073","Position courte nette":"0,55%","Date de début de position":"12/08/2026","Date de début de publication":"13/08/2026","Date de fin de publication":"","Nom du détenteur":"Fund B"},
        # Older active record for Fund B must not be double counted.
        {"ISIN":"FR0000120073","Position courte nette":"0,60%","Date de début de position":"10/08/2026","Date de début de publication":"11/08/2026","Date de fin de publication":"","Nom du détenteur":"Fund B"},
        # Closed historical publication must be excluded.
        {"ISIN":"FR0000120073","Position courte nette":"0,70%","Date de début de position":"01/08/2026","Date de début de publication":"02/08/2026","Date de fin de publication":"05/08/2026","Nom du détenteur":"Fund C"},
    ])
    observations,failures=parse_amf_short_positions(actions,source,observed_at=datetime(2026,8,14,tzinfo=timezone.utc))
    assert not failures
    fields=_fields(observations)
    assert fields["amf_public_short_disclosed_sum_pct"]==0.95
    assert fields["amf_public_short_holder_count"]==2
    assert fields["amf_public_short_latest_position_date"]=="2026-08-12"
    assert fields["amf_public_short_latest_publication_date"]=="2026-08-13"
    assert fields["amf_public_short_days_since_latest_publication"]==1
    assert fields["amf_public_short_open_publication_count"]==2
    assert fields["amf_public_short_proxy_flag"]==1
    assert fields["amf_public_short_not_true_current_interest_flag"]==1
    assert {row["isin"] for row in observations}=={"FR0000120073"}
    assert {row["as_of"] for row in observations}=={"2026-08-13"}


def test_amf_requires_publication_dates_and_holder_columns_instead_of_guessing_current_state():
    actions=pd.DataFrame([{"isin":"FR0000120073"}])
    source=pd.DataFrame([{
        "ISIN":"FR0000120073",
        "Position courte nette":"0,55%",
        "Date de la position":"12/08/2026",
        "Détenteur":"Fund A",
    }])
    observations,failures=parse_amf_short_positions(actions,source,observed_at=datetime(2026,8,14,tzinfo=timezone.utc))
    assert observations==[]
    assert failures and failures[0]["reason"]=="REQUIRED_COLUMNS_NOT_FOUND"
    assert "publication_start" in failures[0]["missing"]
    assert "publication_end" in failures[0]["missing"]


def test_amf_empty_dataset_is_reported_not_imputed():
    actions=pd.DataFrame([{"isin":"FR0000120073"}])
    observations,failures=parse_amf_short_positions(actions,pd.DataFrame())
    assert observations==[]
    assert failures[0]["reason"]=="EMPTY_DATASET"
