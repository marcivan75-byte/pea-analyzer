from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os

import pandas as pd

from .actions_universe_3609_policy import (
    ROOT,
    OUT,
    AUDIT,
    _identity_overlay,
    _calculate_scores,
    _horizon_and_timing,
)

DEFAULT_SOURCE = ROOT / "data" / "external" / "ACTIONS_PEA_3609_LATEST.csv.gz"


def _load_csv_source(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing canonical 3609 source: {path}")
    df = pd.read_csv(path, sep=";", dtype=object, encoding="utf-8-sig", compression="gzip")
    if len(df) != 3609:
        raise RuntimeError(f"Expected exactly 3609 action rows, got {len(df)}")
    if len(df.columns) != 146:
        raise RuntimeError(f"Expected 146 source criteria, got {len(df.columns)}")
    required = {"Nom société", "ISIN", "Cours €", "Score V10 /100", "Verdict V10"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Missing mandatory 3609 source columns: {sorted(missing)}")
    if df["Nom société"].astype(str).str.strip().duplicated().any():
        raise RuntimeError("Duplicate company names in 3609 source")
    return df


def apply_3609_csv_policy(root: Path | None = None, source_path: Path | None = None) -> dict:
    root = root or ROOT
    source = source_path or Path(os.getenv("V204_ACTIONS_3609_SOURCE", str(DEFAULT_SOURCE)))
    df = _load_csv_source(source)
    original_columns = list(df.columns)
    df = _identity_overlay(root, df)
    df = _calculate_scores(df)
    df = _horizon_and_timing(df)
    derived = [c for c in df.columns if c not in original_columns]
    df = df[original_columns + derived]
    out = root / "outputs" / OUT.name
    out.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values("committee_score_3609", ascending=False).to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    audit = {
        "rows": int(len(df)),
        "source_columns": int(len(original_columns)),
        "source_isin_valid": int(df["source_isin_valid"].sum()),
        "identity_methods": {str(k): int(v) for k, v in df["identity_method"].value_counts().to_dict().items()},
        "decision_counts": {str(k): int(v) for k, v in df["decision"].value_counts().to_dict().items()},
        "execution_live_enabled": False,
        "source_path": str(source),
        "source_format": "csv.gz",
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    audit_path = root / "outputs" / "audit" / AUDIT.name
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    print("V20_4_GITOK_ACTIONS_3609_CSV_POLICY", apply_3609_csv_policy())


if __name__ == "__main__":
    main()
