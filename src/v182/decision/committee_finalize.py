from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]


def finalize_committee_fields(root: Path | None = None) -> dict:
    """Finalize committee fields that must be present after all analyst overlays.

    The canonical 12-month target change is expressed both in local target
    currency and in percent. MarketBeat ADR target values remain in their own
    `mb_target_currency` and are never substituted for this local value.
    """
    from v182.io.frames import load_master, save_master
    from v182.reporting.exports import export_master_excel

    root = root or ROOT
    outputs = root / "outputs"
    actions_path = outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    committee_path = outputs / "V18.2_COMMITTEE_ANALYST_MOMENTUM.csv"

    actions = load_master(actions_path).astype(object)
    if "target_change_12m_abs" not in actions.columns:
        addition = pd.DataFrame(
            {"target_change_12m_abs": pd.Series([None] * len(actions), dtype=object)},
            index=actions.index,
        )
        actions = pd.concat([actions, addition], axis=1)
    else:
        actions["target_change_12m_abs"] = actions["target_change_12m_abs"].astype(object)

    current = pd.to_numeric(actions.get("target_price"), errors="coerce")
    previous = pd.to_numeric(actions.get("target_12m_ago"), errors="coerce")
    delta = (current - previous).round(6)
    actions["target_change_12m_abs"] = delta.where(delta.notna(), None)
    observed = int(delta.notna().sum())

    save_master(actions, actions_path)
    export_master_excel(
        actions,
        outputs / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx",
        "V18.2 Actions PEA actualisées",
    )

    committee_rows = 0
    if committee_path.exists() and committee_path.stat().st_size > 0:
        committee = pd.read_csv(committee_path, sep=";", encoding="utf-8-sig", dtype=str)
        values = actions[["isin", "target_change_12m_abs"]].drop_duplicates("isin")
        committee = committee.drop(columns=["target_change_12m_abs"], errors="ignore").merge(
            values, on="isin", how="left"
        )
        anchor = "target_change_12m_pct"
        if anchor in committee.columns:
            columns = list(committee.columns)
            columns.remove("target_change_12m_abs")
            pos = columns.index(anchor)
            columns.insert(pos, "target_change_12m_abs")
            committee = committee[columns]
        committee.to_csv(committee_path, sep=";", index=False, encoding="utf-8-sig")
        committee_rows = len(committee)

    return {
        "target_change_12m_abs_observed": observed,
        "committee_rows": committee_rows,
    }


def main() -> None:
    metrics = finalize_committee_fields()
    print(
        "WAVE_09C_COMMITTEE_FINALIZE — "
        f"target_change_12m_abs={metrics['target_change_12m_abs_observed']} | "
        f"committee_rows={metrics['committee_rows']}"
    )


if __name__ == "__main__":
    main()