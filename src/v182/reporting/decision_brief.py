from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import argparse
import csv
import json


ROOT = Path(__file__).resolve().parents[3]
SELECTED_DECISIONS = {"BUY_CANDIDATE", "WATCH", "WATCH_NOT_TOP2", "REVIEW"}


def _json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _step_health(unified: dict) -> tuple[list[str], list[str]]:
    steps = unified.get("steps") if isinstance(unified.get("steps"), dict) else {}
    failed = sorted(name for name, value in steps.items() if isinstance(value, dict) and value.get("status") == "FAILED")
    skipped = sorted(
        name
        for name, value in steps.items()
        if isinstance(value, dict) and str(value.get("status", "")).startswith("SKIPPED_DEPENDENCY")
    )
    return failed, skipped


def _top_candidates(rows: list[dict[str, str]], limit: int = 10) -> list[dict]:
    selected = [row for row in rows if str(row.get("decision", "")) in SELECTED_DECISIONS]
    selected.sort(key=lambda row: (_number(row.get("score")) is not None, _number(row.get("score")) or -1.0), reverse=True)
    return [
        {
            "asset_class": row.get("asset_class"),
            "horizon": row.get("horizon"),
            "isin": row.get("isin"),
            "name": row.get("name"),
            "decision": row.get("decision"),
            "score": _number(row.get("score")),
            "coverage_pct": _number(row.get("coverage_pct")),
        }
        for row in selected[:limit]
    ]


def _markdown(payload: dict) -> str:
    status_icon = {"READY_FOR_REVIEW": "🟢", "REVIEW_WITH_WARNINGS": "🟠", "BLOCKED": "🔴"}[payload["decision_status"]]
    lines = [
        "# Synthèse décisionnelle — run global",
        "",
        f"## {status_icon} Statut : {payload['decision_status']}",
        "",
        f"- Run : `{payload['run_id']}`",
        f"- Pipeline : **{payload['pipeline_status']}**",
        f"- Étapes : **{payload['steps_success']} réussies**, **{len(payload['failed_steps'])} en échec**, **{len(payload['skipped_dependencies'])} dépendances ignorées**",
        f"- Sélections à examiner : **{payload['selected_count']}**",
        f"- Ordres réels : **DÉSACTIVÉS**",
        "",
        "## Décisions proposées",
        "",
        "| Actif | Horizon | Instrument | Décision | Score | Couverture |",
        "|---|---|---|---|---:|---:|",
    ]
    if payload["top_candidates"]:
        for row in payload["top_candidates"]:
            score = "n/a" if row["score"] is None else f"{row['score']:.1f}"
            coverage = "n/a" if row["coverage_pct"] is None else f"{row['coverage_pct']:.1f}%"
            label = str(row.get("name") or row.get("isin") or "n/a").replace("|", "/")
            lines.append(f"| {row.get('asset_class') or 'n/a'} | {row.get('horizon') or 'n/a'} | {label} | {row.get('decision') or 'n/a'} | {score} | {coverage} |")
    else:
        lines.append("| — | — | Aucune sélection publiée | — | — | — |")
    lines.extend(["", "## Blocages et avertissements", ""])
    issues = payload["blockers"] + payload["warnings"]
    lines.extend([f"- {item}" for item in issues] if issues else ["- Aucun blocage technique détecté ; revue humaine requise avant toute décision."])
    lines.extend(
        [
            "",
            "## Garde-fous",
            "",
            "- Cette synthèse est en lecture seule et ne modifie aucun score, poids, seuil ou décision.",
            "- Les modules SHADOW restent sans influence sur les décisions canoniques.",
            "- Aucun ordre réel n’est émis ; la décision finale demeure humaine.",
            "",
            "## Artefacts de référence",
            "",
            "- `outputs/committee_master/COMMITTEE_DECISIONS.csv`",
            "- `outputs/mobile/ANDROID_CI_CONTROL_CENTER.md`",
            "- `outputs/committee_master/CI_PC_EXPLAINABILITY.xlsx`",
            "- `outputs/unified/UNIFIED_SUMMARY_LATEST.json`",
            "- `outputs/decision_brief/DECISION_BRIEF.json`",
            "",
        ]
    )
    return "\n".join(lines)


def run(root: Path = ROOT) -> dict:
    unified = _json(root / "outputs" / "unified" / "UNIFIED_SUMMARY_LATEST.json")
    decisions = _rows(root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv")
    explainability = _json(root / "outputs" / "audit" / "CI_EXPLAINABILITY_AUDIT.json")
    failed, skipped = _step_health(unified)
    steps = unified.get("steps") if isinstance(unified.get("steps"), dict) else {}
    pipeline_status = str(unified.get("status") or "MISSING")
    blockers: list[str] = []
    warnings: list[str] = []
    if not unified:
        blockers.append("Résumé global absent ou illisible.")
    if pipeline_status != "SUCCESS":
        blockers.append(f"Pipeline global non réussi : {pipeline_status}.")
    if failed:
        blockers.append("Étapes en échec : " + ", ".join(failed) + ".")
    if skipped:
        blockers.append("Dépendances ignorées : " + ", ".join(skipped) + ".")
    reconstruction = explainability.get("reconstruction") if isinstance(explainability.get("reconstruction"), dict) else {}
    if explainability and not reconstruction.get("within_0_02_points", False):
        warnings.append("La reconstruction des scores publiés dépasse la tolérance de 0,02 point ou manque de données.")
    if not decisions:
        warnings.append("Aucune décision canonique lisible n’a été publiée.")
    selected = [row for row in decisions if str(row.get("decision", "")) in SELECTED_DECISIONS]
    counts = Counter(str(row.get("decision", "MISSING")) for row in decisions)
    if blockers:
        decision_status = "BLOCKED"
    elif warnings:
        decision_status = "REVIEW_WITH_WARNINGS"
    else:
        decision_status = "READY_FOR_REVIEW"
    payload = {
        "version": "DECISION_BRIEF_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": unified.get("run_id", "UNKNOWN"),
        "decision_status": decision_status,
        "pipeline_status": pipeline_status,
        "steps_success": sum(1 for value in steps.values() if isinstance(value, dict) and value.get("status") == "SUCCESS"),
        "failed_steps": failed,
        "skipped_dependencies": skipped,
        "decision_counts": dict(sorted(counts.items())),
        "selected_count": len(selected),
        "top_candidates": _top_candidates(decisions),
        "blockers": blockers,
        "warnings": warnings,
        "real_orders_enabled": False,
        "score_or_decision_mutation": False,
    }
    outdir = root / "outputs" / "decision_brief"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "DECISION_BRIEF.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "DECISION_BRIEF.md").write_text(_markdown(payload), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    print(json.dumps(run(Path(args.root)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

