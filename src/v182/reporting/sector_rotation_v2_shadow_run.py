from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.features.instrument_theme_v2 import build_mapping_worklist, load_instrument_theme_mapping
from v182.features.sector_rotation_v2_final import append_history, build_sector_rotation_v2, load_config
from v182.features.sector_rotation_v2_membership import append_membership_history, build_membership_snapshot
from v182.features.theme_propagation_v2 import load_transmission_graph, propagate_theme_scores
from v182.features.theme_rotation_auto_v2 import build_theme_rotation_shadow, load_auto_theme_rules
from v182.reporting.sector_rotation_v2_compare import write_comparison
from v182.reporting.sector_rotation_v2_report import write_shadow_report
from v182.reporting.sector_rotation_v2_validation_run import run as run_pit_oos_validation


ROOT = Path(__file__).resolve().parents[3]


def _read_master(root: Path, asset: str) -> tuple[pd.DataFrame, str]:
    enriched = root / "outputs" / f"V18.2_PEA_{asset}_MASTER_ENRICHED.csv"
    fallback = root / "inputs" / f"V18.2_PEA_{asset}_MASTER.csv"
    path = enriched if enriched.exists() else fallback
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False), str(path.relative_to(root))


def _load_history(path: Path) -> pd.DataFrame | None:
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False) if path.exists() else None


def _run_validation_safely(root: Path) -> dict:
    try:
        return run_pit_oos_validation(root)
    except Exception as exc:
        return {
            "status": "VALIDATION_RUNTIME_ERROR",
            "error": type(exc).__name__,
            "detail": str(exc)[:500],
            "promotion_ready": False,
            "decision_influence": 0.0,
            "live_orders_enabled": False,
        }


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
    membership_history_path = statedir / "SECTOR_ROTATION_V2_CONSTITUENTS.csv"
    theme_history_path = statedir / "THEME_ROTATION_V2_HISTORY.csv"
    history = _load_history(history_path)
    theme_history = _load_history(theme_history_path)
    as_of = datetime.now(timezone.utc).date().isoformat()
    result = build_sector_rotation_v2(actions, cfg, history=history, as_of=as_of)

    snapshot_path = outdir / "V2_SECTOR_ROTATION_SHADOW.csv"
    diagnostic_path = auditdir / "V2_SECTOR_ROTATION_SHADOW.json"
    comparison_path = outdir / "V1_V2_COMPARISON.csv"
    comparison_audit_path = auditdir / "V1_V2_SECTOR_ROTATION_COMPARISON.json"
    committee_dir = outdir / "committee_shadow"
    theme_snapshot_path = outdir / "V2_THEME_ROTATION_SHADOW.csv"
    theme_tags_path = outdir / "V2_THEME_DIRECT_TAGS.csv"
    theme_propagation_path = outdir / "V2_THEME_PROPAGATION_SHADOW.csv"
    theme_committee_dir = outdir / "theme_committee_shadow"
    mapping_path = root / "config" / "SECTOR_ROTATION_V2_INSTRUMENT_THEME_MAPPING.csv"
    mapping_worklist_path = gapsdir / "SECTOR_ROTATION_V2_THEME_MAPPING_WORKLIST.csv"

    result.sectors.to_csv(snapshot_path, sep=";", index=False, encoding="utf-8-sig")
    comparison = write_comparison(
        root / "outputs" / "V21_3_SECTOR_ROTATION.csv",
        snapshot_path,
        comparison_path,
        comparison_audit_path,
    )
    comparison_snapshot = pd.read_csv(comparison_path, sep=";", encoding="utf-8-sig", low_memory=False)
    append_history(comparison_snapshot, history_path)
    membership_snapshot = build_membership_snapshot(
        actions,
        result.sectors["sector"].tolist(),
        as_of=as_of,
        model_version=str(cfg.get("version", "SECTOR_ROTATION_V2")),
    )
    append_membership_history(membership_snapshot, membership_history_path)
    committee_report = write_shadow_report(result.sectors, committee_dir)

    rules = load_auto_theme_rules(root / "config" / "SECTOR_ROTATION_V2_AUTO_THEME_RULES.csv")
    themes, theme_summary, direct_tags = build_theme_rotation_shadow(
        actions,
        rules,
        cfg,
        history=theme_history,
        as_of=as_of,
    )
    themes.to_csv(theme_snapshot_path, sep=";", index=False, encoding="utf-8-sig")
    direct_tags.to_csv(theme_tags_path, sep=";", index=False, encoding="utf-8-sig")

    if not themes.empty:
        theme_history_rows = themes.rename(columns={"theme_id": "sector"}).copy()
        append_history(theme_history_rows, theme_history_path)
        theme_committee = write_shadow_report(themes.rename(columns={"theme_id": "sector"}), theme_committee_dir)
        graph = load_transmission_graph(root / "config" / "SECTOR_ROTATION_V2_THEME_TRANSMISSION.csv")
        propagation, propagation_summary = propagate_theme_scores(themes, graph)
    else:
        theme_committee = {"status": "EMPTY", "blocks": {}, "decision_influence": 0.0}
        propagation = pd.DataFrame()
        propagation_summary = {"status": "NO_THEME_SCORES", "decision_influence": 0.0}
    propagation.to_csv(theme_propagation_path, sep=";", index=False, encoding="utf-8-sig")

    mapping = load_instrument_theme_mapping(mapping_path, as_of=as_of)
    action_worklist = build_mapping_worklist(actions, mapping, universe="ACTION")
    etf_worklist = build_mapping_worklist(etfs, mapping, universe="ETF")
    pd.concat([action_worklist, etf_worklist], ignore_index=True).to_csv(
        mapping_worklist_path, sep=";", index=False, encoding="utf-8-sig"
    )
    pit_oos_validation = _run_validation_safely(root)

    summary = dict(result.diagnostic)
    summary.update(
        {
            "source_actions": action_source,
            "source_etfs": etf_source,
            "snapshot_path": str(snapshot_path.relative_to(root)),
            "history_path": str(history_path.relative_to(root)),
            "history_includes_v1_baseline": True,
            "membership_history_path": str(membership_history_path.relative_to(root)),
            "membership_snapshot_rows": int(len(membership_snapshot)),
            "comparison_path": str(comparison_path.relative_to(root)),
            "committee_shadow_path": str(committee_dir.relative_to(root)),
            "theme_snapshot_path": str(theme_snapshot_path.relative_to(root)),
            "theme_history_path": str(theme_history_path.relative_to(root)),
            "theme_tags_path": str(theme_tags_path.relative_to(root)),
            "theme_propagation_path": str(theme_propagation_path.relative_to(root)),
            "theme_committee_shadow_path": str(theme_committee_dir.relative_to(root)),
            "mapping_worklist_path": str(mapping_worklist_path.relative_to(root)),
            "unmapped_actions": int(len(action_worklist)),
            "unmapped_etfs": int(len(etf_worklist)),
            "comparison": comparison,
            "committee_report": committee_report,
            "theme_summary": theme_summary,
            "theme_committee_report": theme_committee,
            "theme_propagation_summary": propagation_summary,
            "pit_oos_validation": pit_oos_validation,
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
