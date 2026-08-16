from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import pandas as pd


REPORT_BLOCKS = {
    "NEW_ROTATIONS": lambda df: df["state"].isin(["EARLY_ROTATION", "CONFIRMED_ROTATION"]),
    "BEST_RISK_REWARD": lambda df: df["new_position_action"].isin(["PRIORITY_BUY_ZONE", "BUY_ZONE", "ACCUMULATE_ON_WEAKNESS"]),
    "CONFIRMED_LEADERS": lambda df: df["state"].isin(["LEADERSHIP", "MATURE_LEADERSHIP"]),
    "PROMISING_BUT_OVERVALUED": lambda df: df["warnings"].astype(str).str.contains("PROMISING_BUT_OVERVALUED", regex=False),
    "DETERIORATING": lambda df: df["state"].isin(["DISTRIBUTION", "ROTATION_OUT"]) | df["correction_alert"].astype(bool),
    "REENTRY": lambda df: df["reentry_state"].isin(["WATCH_REENTRY", "REENTRY_FORMING", "REENTRY_READY"]),
}


def write_shadow_report(sectors: pd.DataFrame, output_dir: str | Path) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if sectors.empty:
        summary = {"status": "EMPTY", "blocks": {}, "decision_influence": 0.0}
        (output / "SECTOR_ROTATION_V2_COMMITTEE_SHADOW.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary

    preferred_columns = [
        "rank",
        "sector",
        "RLS",
        "RLS_velocity",
        "RLS_acceleration",
        "SQS",
        "CTS",
        "STS",
        "MCS",
        "AVCR",
        "valuation_state",
        "RARS",
        "DQS",
        "state",
        "warnings",
        "warning_confidence",
        "new_position_action",
        "existing_position_action",
        "reentry_readiness",
        "reentry_state",
        "as_of",
    ]
    columns = [column for column in preferred_columns if column in sectors.columns]
    counts: dict[str, int] = {}
    top_rows: dict[str, list[dict[str, Any]]] = {}
    for name, predicate in REPORT_BLOCKS.items():
        mask = predicate(sectors)
        block = sectors.loc[mask, columns].copy()
        if "RARS" in block.columns:
            block = block.sort_values("RARS", ascending=False)
        block.to_csv(output / f"{name}.csv", sep=";", index=False, encoding="utf-8-sig")
        counts[name] = int(len(block))
        top_rows[name] = block.head(10).to_dict("records")

    summary = {
        "status": "OK",
        "sector_count": int(len(sectors)),
        "blocks": counts,
        "top": top_rows,
        "decision_influence": 0.0,
        "required_warning_block": "PROMISING_BUT_OVERVALUED",
    }
    (output / "SECTOR_ROTATION_V2_COMMITTEE_SHADOW.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return summary
