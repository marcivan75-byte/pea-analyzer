"""Rapports O/R HEBDO automatiques — SHADOW only, influence 0."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import shutil

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
CHALLENGER = Path("outputs/committee_master/OBJECTIVES_RISK_CHALLENGER_V2.csv")
AUDIT = Path("outputs/audit/OR_HEBDO_REPORT_V1.json")
REPORT_MD = Path("outputs/mobile/OR_HEBDO_REPORT.md")
ANDROID_MD = Path("outputs/mobile/ANDROID_OR_HEBDO_SHADOW.md")


def _read(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _alias_dated(root: Path, date: str) -> list[str]:
    mapping = {
        f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_{date}.csv": "outputs/committee_master/OR_RANKING_HEBDO_SHADOW_LATEST.csv",
        f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_ETF_ONLY_{date}.csv": "outputs/committee_master/OR_RANKING_HEBDO_SHADOW_ETF_ONLY_LATEST.csv",
        f"outputs/committee_master/OR_RANKING_HEBDO_SHADOW_ACTION_CT_ONLY_{date}.csv": "outputs/committee_master/OR_RANKING_HEBDO_SHADOW_ACTION_CT_ONLY_LATEST.csv",
        f"outputs/committee_master/OR_RANKING_ETF_MT_SHADOW_{date}.csv": "outputs/committee_master/OR_RANKING_ETF_MT_SHADOW_LATEST.csv",
        f"outputs/committee_master/OR_RANKING_DAILY_SHADOW_{date}.csv": "outputs/committee_master/OR_RANKING_DAILY_SHADOW_LATEST.csv",
        f"outputs/committee_master/SECTOR_OR_RANKING_SHADOW_{date}.csv": "outputs/committee_master/SECTOR_OR_RANKING_SHADOW_LATEST.csv",
        f"outputs/committee_master/SECTOR_OR_AGGREGATE_{date}.csv": "outputs/committee_master/SECTOR_OR_AGGREGATE_LATEST.csv",
    }
    copied = []
    for source_rel, dest_rel in mapping.items():
        source = root / source_rel
        if source.exists() and source.stat().st_size:
            dest = root / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            copied.append(dest_rel)
    return copied


def _rows(frame: pd.DataFrame, label: str) -> list[str]:
    if frame.empty:
        return [f"- {label}: aucun instrument."]
    lines = [f"### {label}", ""]
    cols = [c for c in (
        "name", "isin", "OR_COMPOSITE_SHADOW", "OR_HEBDO_LABEL", "OR_ENTRY_ACTION_SHADOW",
        "OR_RISK_VERDICT", "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY", "OR_RELIABILITY_0_100",
    ) if c in frame]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join(["---"] * len(cols)) + "|")
    for _, row in frame.head(10).iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("nan", "INDISPONIBLE") for c in cols) + " |")
    lines.append("")
    return lines


def run(root: Path = ROOT) -> dict:
    generated = datetime.now(timezone.utc).isoformat()
    date = datetime.now(timezone.utc).date().isoformat()
    frame = _read(root / CHALLENGER)
    aliases = _alias_dated(root, date)
    labels = frame["OR_HEBDO_LABEL"].value_counts(dropna=False).to_dict() if "OR_HEBDO_LABEL" in frame else {}
    actions = frame["OR_ENTRY_ACTION_SHADOW"].value_counts(dropna=False).to_dict() if "OR_ENTRY_ACTION_SHADOW" in frame else {}
    ranked = frame.sort_values("OR_COMPOSITE_SHADOW", ascending=False, na_position="last") if "OR_COMPOSITE_SHADOW" in frame else frame
    asset = ranked.get("asset_class", pd.Series("", index=ranked.index)).astype(str).str.upper()
    action = ranked[asset.eq("ACTION")]
    etf = ranked[asset.eq("ETF")]
    lines = [
        "# Rapport O/R HEBDO SHADOW",
        "",
        f"Généré: {generated}",
        "",
        "Mode: SHADOW — decision_influence = 0 — real_orders_enabled = false",
        "",
        f"- instruments: {len(frame)}",
        f"- labels: {labels}",
        f"- actions SHADOW: {actions}",
        "",
        *_rows(action, "Top ACTION"),
        *_rows(etf, "Top ETF"),
        "Aucun ordre réel. Aucune écriture dans COMMITTEE_DECISIONS.",
        "",
    ]
    for path in (root / REPORT_MD, root / ANDROID_MD, root / AUDIT):
        path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    (root / REPORT_MD).write_text(text, encoding="utf-8")
    (root / ANDROID_MD).write_text(text, encoding="utf-8")
    payload = {
        "status": "SUCCESS" if not frame.empty else "SUCCESS_EMPTY_INPUT",
        "generated_at_utc": generated,
        "rows": int(len(frame)),
        "labels": labels,
        "entry_actions": actions,
        "aliases": aliases,
        "reports": [str(REPORT_MD), str(ANDROID_MD)],
        "shadow_only": True,
        "real_orders_enabled": False,
        "score_influence": 0.0,
    }
    (root / AUDIT).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
