from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "data/reference/V21.0_ACTIONS_PEA_CONFIG.json"
TARGET = ROOT / "outputs/V21.0_ACTIONS_PEA_1829_PREPARED.csv"

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
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    expected = int(cfg["canonical_universe_size"])
    df = pd.read_csv(TARGET, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != expected or df["isin"].astype(str).nunique() != expected:
        raise RuntimeError(f"V21 schema preparation requires canonical {expected}")
    for field in TEXT_FIELDS:
        if field not in df.columns:
            df[field] = ""
        else:
            df[field] = df[field].astype("object")
    df.to_csv(TARGET, sep=";", index=False, encoding="utf-8-sig")
    print("V21_ACTIONS_BACKFILL_SCHEMA_OK", {"rows": len(df), "text_fields": len(TEXT_FIELDS)})


if __name__ == "__main__":
    main()
