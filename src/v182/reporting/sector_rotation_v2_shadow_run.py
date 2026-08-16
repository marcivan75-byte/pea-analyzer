from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.features.sector_rotation_v2 import append_history, build_sector_rotation_v2, load_config
from v182.reporting.sector_rotation_v2_compare import write_comparison


ROOT = Path(__file__).resolve().parents[3]


def _read_actions(root: Path) -> pd.DataFrame:
    enriched = root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    fallback = root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv"
    path = enriched if enriched.exists() else fallback
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False)


def run(root: Path = ROOT) -> dict:
    cfg_path = root / "config" / "SECTOR_ROTATION_V2_SHADOW.json"
    cfg = load_config(cfg_path)
    actions = _read_actions(root)

    outdir = root / "outputs" / "sector_rotation"
    auditdir = root / "outputs" / "audit"
    statedir = root / "state" / "sector_rotation_v2"
    outdir.mkdir(parents=True, exist_ok=True)
    auditdir.mkdir(parents=True, exist_ok=True)
    statedir.mkdir(parents=True, exist_ok=True)

    history_path = statedir / "SECTOR_ROTATION_V2_HISTORY.csv"
    history = pd.read_csv(history_path, sep=";", encoding="utf-8-sig", low_memory=False) if history_path.exists() else None
    as_of = datetime.now(timezone.utc).date().isoformat()
    result = build_sector_rotation_v2(actions, cfg, history=history, as_of=as_of)

    snapshot_path = outdir / "V2_SECTOR_ROTATION_SHADOW.csv"
    diagnostic_path = auditdir / "V2_SECTOR_ROTATION_SHADOW.json"
    comparison_path = outdir / "V1_V2_COMPARISON.csv"
    comparison_audit_path = auditdir / "V1_V2_SECTOR_ROTATION_COMPARISON.json"
    result.sectors.to_csv(snapshot_path, sep=";", index=False, encoding="utf-8-sig")
    append_history(result.sectors, history_path)

    comparison = write_comparison(
        root / "outputs" / "V21_3_SECTOR_ROTATION.csv",
        snapshot_path,
        comparison_path,
        comparison_audit_path,
    )

    summary = dict(result.diagnostic)
    summary.update(
        {
            "source_actions": "outputs/V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv" if (root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv").exists() else "inputs/V18.2_PEA_ACTIONS_MASTER.csv",
            "snapshot_path": str(snapshot_path.relative_to(root)),
            "history_path": str(history_path.relative_to(root)),
            "comparison_path": str(comparison_path.relative_to(root)),
            "comparison": comparison,
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
