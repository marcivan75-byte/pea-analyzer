from __future__ import annotations

from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_CI_LIGHT_FINALIZE_V21_8_3"
OUTPUT_COLUMNS = [
    "asset_class", "isin", "name", "decision_ct", "score_ct", "decision_tct", "score_tct",
    "boursorama_n_analysts", "boursorama_recommendation", "boursorama_target_upside_pct",
    "morningstar_rating", "investing_daily", "investing_weekly", "investing_monthly",
    "boursorama_url", "investing_url", "investing_technical_url", "investing_age_hours", "selection_rule",
]


def run(root: Path = ROOT) -> dict:
    csv_path = root / "outputs" / "daily_tct_ct" / "CI_LIGHT_V21_8_2.csv"
    audit_path = root / "outputs" / "audit" / "DAILY_CI_LIGHT_V21_8_2.json"
    artifact_audit = root / "outputs" / "daily_tct_ct" / "CI_LIGHT_AUDIT_V21_8_2.json"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    header_fixed = False
    try:
        frame = pd.read_csv(csv_path, sep=";", encoding="utf-8-sig", low_memory=False)
    except (FileNotFoundError, pd.errors.EmptyDataError, UnicodeDecodeError):
        frame = pd.DataFrame(columns=OUTPUT_COLUMNS)
        frame.to_csv(csv_path, sep=";", index=False, encoding="utf-8-sig")
        header_fixed = True
    else:
        missing = [column for column in OUTPUT_COLUMNS if column not in frame.columns]
        if frame.empty and missing:
            frame = pd.DataFrame(columns=OUTPUT_COLUMNS)
            frame.to_csv(csv_path, sep=";", index=False, encoding="utf-8-sig")
            header_fixed = True

    audit = {}
    if audit_path.exists():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            audit = {}
    artifact_payload = dict(audit)
    artifact_payload.update({
        "finalizer_version": VERSION,
        "csv_schema_columns": OUTPUT_COLUMNS,
        "csv_header_present_when_empty": True,
        "csv_header_repaired": bool(header_fixed),
        "artifact_audit_copy": str(artifact_audit.relative_to(root)),
    })
    artifact_audit.write_text(json.dumps(artifact_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {
        "status": "SUCCESS",
        "version": VERSION,
        "csv_header_present_when_empty": True,
        "csv_header_repaired": bool(header_fixed),
        "artifact_audit_copy": str(artifact_audit.relative_to(root)),
        "selected_rows": int(len(frame)),
        "external_collection_calls": 0,
        "score_or_decision_mutation": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }
