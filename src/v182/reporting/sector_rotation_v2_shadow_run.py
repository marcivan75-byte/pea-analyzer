from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.features.instrument_theme_v2 import build_mapping_worklist, load_instrument_theme_mapping
from v182.features.sector_rotation_v2 import append_history, build_sector_rotation_v2, load_config
from v182.reporting.sector_rotation_v2_compare import write_comparison
from v182.reporting.sector_rotation_v2_report import write_shadow_report


ROOT = Path(__file__).resolve().parents[3]


def _read_master(root: Path, asset: str) -> tuple[pd.DataFrame, str]:
    enriched = root / "outputs" / f"V18.2_PEA_{asset}_MASTER_ENRICHED.csv"
    fallback = root / "inputs" / f"V18.2_PEA_{asset}_MASTER.csv"
    path = enriched if enriched.exists() else fallback
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False), str(path.relative_to(root))


def run(root: Path = ROOT) -> dict:
    cfg = load_config(root / "config" / "SECTOR_ROTATION_V2_SHADOW.json")
    actions, action_source = _read_master(root, "ACTIONS")
    etfs, etf_source = _read_master(root, "ETF")

    outdir = root / "outputs" / "sector_rotation"
    auditdir = root / "outputs" / "audit"
    gapsdir = root / "outputs" / "gaps"
    statedir = root / "state" / "sector_rotation_v2"
    for directory in (outdir, auditdir, gapsdir, statedir):
        directory.mkdir(parents=True, exist_ok=True)

    history_path = statedir / "SECTOR_ROTATION_V2_HISTORY.csv"
    history = pd.read_csv(history_path, sep=";", encoding="utf-8-sig", low_memory=False) if history_path.exists() else None
    as_of = datetime.now(timezone.utc).date().isoformat()
    result = build_sector_rotation_v2(actions, cfg, history=history, as_of=as_of)

    snapshot_path = outdir / "V2_SECTOR_ROTATION_SHADOW.csv"
    diagnostic_path = auditdir / "V2_SECTOR_ROTATION_SHADOW.json"
    comparison_path = outdir / "V1_V2_COMPARISON.csv"
    comparison_audit_path = auditdir / "V1_V2_SECTOR_ROTATION_COMPARISON.json"
    committee_dir = outdir / "committee_shadow"
    mapping_path = root / "config" / "SECTOR_ROTATION_V2_INSTRUMENT_THEME_MAPPING.csv"
    mapping_worklist_path = gapsdir / "SECTOR_ROTATION_V2_THEME_MAPPING_WORKLIST.csv"

    result.sectors.to_csv(snapshot_path, sep=";", index=False, encoding="utf-8-sig")
    append_history(result.sectors, history_path)

    comparison = write_comparison(
        root / "outputs" / "V21_3_SECTOR_ROTATION.csv",
        snapshot_path,
        comparison_path,
        comparison_audit_path,
    )
    committee_report = write_shadow_report(result.sectors, committee_dir)

    mapping = load_instrument_theme_mapping(mapping_path, as_of=as_of)
    action_worklist = build_mapping_worklist(actions, mapping, universe="ACTION")
    etf_worklist = build_mapping_worklist(etfs, mapping, universe="ETF")
    pd.concat([action_worklist, etf_worklist], ignore_index=True).to_csv(
        mapping_worklist_path, sep=";", index=False, encoding="utf-8-sig"
    )

    summary = dict(result.diagnostic)
    summary.update(
        {
            "source_actions": action_source,
            "source_etfs": etf_source,
            "snapshot_path": str(snapshot_path.relative_to(root)),
            "history_path": str(history_path.relative_to(root)),
            "comparison_path": str(comparison_path.relative_to(root)),
            "committee_shadow_path": str(committee_dir.relative_to(root)),
            "mapping_worklist_path": str(mapping_worklist_path.relative_to(root)),
            "unmapped_actions": int(len(action_worklist)),
            "unmapped_etfs": int(len(etf_worklist)),
            "comparison": comparison,
            "committee_report": committee_report,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "decision_influence": 0.0,
            "live_orders_enabled": False,
        }
    )
    diagnostic_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    payload = run()
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
