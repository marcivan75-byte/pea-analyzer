from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd

from v182.audit.canonical_universe import filter_actions
from v182.audit.master_data import isin_checksum_valid, normalize_isin
from v182.io.frames import load_master


def run(root: Path) -> dict:
    legacy = load_master(root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
    canonical = filter_actions(legacy, root / "config" / "V21_3_ACTION_UNIVERSE_1829_ISINS.parts")
    excluded = canonical.excluded.copy().reset_index(drop=True)
    if "isin" not in excluded.columns:
        raise RuntimeError("EXCLUDED_LEGACY_ACTIONS_MISSING_ISIN")

    excluded["normalized_isin"] = excluded["isin"].map(normalize_isin)
    excluded["isin_checksum_valid"] = excluded["normalized_isin"].map(isin_checksum_valid)
    excluded["quarantine_reason"] = "NOT_IN_CANONICAL_V21_3_ACTION_UNIVERSE"
    excluded["scoring_eligible"] = False

    preferred = [
        column for column in (
            "isin", "normalized_isin", "isin_checksum_valid", "name", "yahoo_ticker",
            "country", "euronext_mic", "pea_confidence", "evidence_level",
            "isin_correction_status", "original_isin", "isin_validation_source",
            "isin_validation_as_of", "quarantine_reason", "scoring_eligible",
        ) if column in excluded.columns
    ]
    out = excluded[preferred].copy()

    outdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / "MASTER_DATA_EXCLUDED_LEGACY_ACTIONS.csv"
    out.to_csv(csv_path, sep=";", encoding="utf-8-sig", index=False)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "legacy_action_rows": int(len(legacy)),
        "canonical_action_rows": int(len(canonical.included)),
        "excluded_legacy_rows": int(len(out)),
        "excluded_valid_checksum_rows": int(out["isin_checksum_valid"].sum()) if len(out) else 0,
        "excluded_invalid_checksum_rows": int((~out["isin_checksum_valid"]).sum()) if len(out) else 0,
        "policy": "EXCLUDED_LEGACY_ROWS_ARE_QUARANTINED_AND_MUST_NOT_ENTER_SCORING_OR_BACKTEST_IDENTITY_JOINS",
        "output": str(csv_path.relative_to(root)),
    }
    (outdir / "MASTER_DATA_EXCLUSIONS_SUMMARY.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run(Path(__file__).resolve().parents[3])
