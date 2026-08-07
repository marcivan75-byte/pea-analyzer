import pandas as pd

from v182.decision.committee_finalize import finalize_committee_fields


def test_finalize_adds_12m_absolute_target_change_to_master_and_committee(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    actions = pd.DataFrame([{
        "isin": "FR0000000001",
        "name": "TEST",
        "target_price": "120",
        "target_12m_ago": "100",
        "target_change_12m_pct": "20",
    }])
    actions.to_csv(
        outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",
        sep=";", index=False, encoding="utf-8-sig",
    )
    pd.DataFrame([{
        "isin": "FR0000000001",
        "target_change_12m_pct": "20",
    }]).to_csv(
        outputs / "V18.2_COMMITTEE_ANALYST_MOMENTUM.csv",
        sep=";", index=False, encoding="utf-8-sig",
    )

    metrics = finalize_committee_fields(tmp_path)
    assert metrics["target_change_12m_abs_observed"] == 1

    enriched = pd.read_csv(
        outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",
        sep=";", encoding="utf-8-sig",
    )
    committee = pd.read_csv(
        outputs / "V18.2_COMMITTEE_ANALYST_MOMENTUM.csv",
        sep=";", encoding="utf-8-sig",
    )
    assert float(enriched.loc[0, "target_change_12m_abs"]) == 20.0
    assert float(committee.loc[0, "target_change_12m_abs"]) == 20.0
    columns = list(committee.columns)
    assert columns.index("target_change_12m_abs") < columns.index("target_change_12m_pct")
