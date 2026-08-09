from __future__ import annotations

from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "outputs/V21.0_ACTIONS_PEA_1429_PREPARED.csv"

TEXT_FIELDS = [
    "consensus_label_v21",
    "yahoo_backfill_status",
    "consensus_source_v21",
    "target_source_v21",
    "analyst_coverage_status_v21",
    "analyst_coverage_source_v21",
    "finnhub_symbol_v21",
    "finnhub_checked_at_utc",
    "eodhd_symbol_v21",
    "eodhd_checked_at_utc",
]


def main() -> None:
    df = pd.read_csv(TARGET, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != 1429 or df["isin"].astype(str).nunique() != 1429:
        raise RuntimeError("V21 schema preparation requires canonical 1429")
    for field in TEXT_FIELDS:
        if field not in df.columns:
            df[field] = ""
        else:
            df[field] = df[field].astype("object")
    df.to_csv(TARGET, sep=";", index=False, encoding="utf-8-sig")
    print("V21_ACTIONS_BACKFILL_SCHEMA_OK", {"rows": len(df), "text_fields": len(TEXT_FIELDS)})


if __name__ == "__main__":
    main()
