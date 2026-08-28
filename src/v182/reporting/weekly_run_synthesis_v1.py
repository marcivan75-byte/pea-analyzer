"""Synthèse en ligne du run Friday : timings, CI, CI LIGHT."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = Path("outputs/audit/WEEKLY_OPERATIONAL_RUNTIME_V4_4.json")
CORE_AUDIT = Path("outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_3.json")
CI_PATHS = (
    Path("outputs/committee_master/CI_RESULTS_CHALLENGER_V2.csv"),
    Path("outputs/committee_master/CI_SELECTION_ALL_V4.csv"),
)
LIGHT_PATHS = (
    Path("outputs/committee_master/CI_LIGHT_RESULTS_CHALLENGER_V2.csv"),
    Path("outputs/committee_master/CI_LIGHT_V4.csv"),
)
OUT_MD = Path("outputs/mobile/WEEKLY_RUN_SYNTHESIS.md")
OUT_JSON = Path("outputs/audit/WEEKLY_RUN_SYNTHESIS.json")


def _read_csv(root: Path, candidates: tuple[Path, ...]) -> tuple[pd.DataFrame, str | None]:
    for relative in candidates:
        path = root / relative
        if path.exists() and path.stat().st_size:
            return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False), str(relative)
    return pd.DataFrame(), None


def _read_json(root: Path, relative: Path) -> dict:
    path = root / relative
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _table(frame: pd.DataFrame, cols: list[str], n: int = 15) -> list[str]:
    if frame.empty:
        return ["Aucun instrument."]
    use = [c for c in cols if c in frame.columns]
    if not use:
        use = [c for c in frame.columns if c != "isin"][:8]
    lines = ["| " + " | ".join(use) + " |", "|" + "|".join(["---"] * len(use)) + "|"]
    for _, row in frame.head(n).iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("nan", "INDISPONIBLE") for c in use) + " |")
    return lines


def run(root: Path = ROOT) -> dict:
    generated = datetime.now(timezone.utc).isoformat()
    runtime = _read_json(root, RUNTIME)
    core = _read_json(root, CORE_AUDIT)
    ci, ci_src = _read_csv(root, CI_PATHS)
    light, light_src = _read_csv(root, LIGHT_PATHS)
    steps = runtime.get("steps_seconds") or {}
    if core.get("ci_seconds") is not None:
        steps = {"ci_core": core.get("ci_seconds"), "ci_light": core.get("ci_light_seconds"), **steps}
    timing_rows = ["| étape | secondes |", "|---|---|"]
    for name, value in steps.items():
        timing_rows.append(f"| {name} | {value} |")
    timing_rows.append(f"| total | {runtime.get('total_seconds', '')} |")
    ci_cols = [
        "name", "isin", "asset_class", "horizon", "score",
        "CI_CONFIDENCE_SCORE_V22_2_1", "CI_SELECTION_GATE_STATUS_V4",
        "OR_COMPOSITE_SHADOW", "OR_HEBDO_LABEL",
    ]
    light_cols = [
        "name", "isin", "asset_class", "horizon", "score",
        "CI_LIGHT_REASON", "CI_LIGHT_TRADINGVIEW_DAILY",
        "CI_LIGHT_TRADINGVIEW_WEEKLY", "CI_LIGHT_TRADINGVIEW_MONTHLY",
        "OR_COMPOSITE_SHADOW",
    ]
    lines = [
        "# Synthèse HEBDO V4.4 — CI / CI LIGHT / timings",
        "",
        f"Généré: {generated}",
        f"Statut runner: {runtime.get('status', 'UNKNOWN')}",
        f"Total: {runtime.get('total_seconds', 'n/a')} s / cible {runtime.get('target_seconds', 1200)} s",
        "",
        "Mode: SHADOW pour O/R — aucun ordre réel.",
        "",
        "## Temps par étape",
        "",
        *timing_rows,
        "",
        f"## CI ({len(ci)} lignes, source={ci_src})",
        "",
        *_table(ci, ci_cols),
        "",
        f"## CI LIGHT ({len(light)} lignes, source={light_src})",
        "",
        *_table(light, light_cols),
        "",
    ]
    text = "\n".join(lines)
    for path in (root / OUT_MD, root / OUT_JSON):
        path.parent.mkdir(parents=True, exist_ok=True)
    (root / OUT_MD).write_text(text, encoding="utf-8")
    payload = {
        "status": "SUCCESS",
        "generated_at_utc": generated,
        "ci_rows": int(len(ci)),
        "ci_light_rows": int(len(light)),
        "ci_source": ci_src,
        "ci_light_source": light_src,
        "total_seconds": runtime.get("total_seconds"),
        "steps_seconds": steps,
        "report": str(OUT_MD),
        "real_orders_enabled": False,
    }
    (root / OUT_JSON).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
