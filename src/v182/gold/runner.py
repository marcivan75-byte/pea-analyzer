from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .scoring import evaluate_snapshot, load_config, validate_config

DEFAULT_INPUT = Path("data/import/gold/V21.1_GOLD_OBSERVATIONS.json")
DEFAULT_OUTPUT = Path("outputs/gold/V21.1_GOLD_COMMITTEE.json")
DEFAULT_SUMMARY = Path("outputs/gold/V21.1_GOLD_COMMITTEE_SUMMARY.md")


def _empty_snapshot() -> dict[str, Any]:
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "criteria": {},
        "regime": None,
        "active_gates": ["DATA_QUALITY"],
        "qds": 0,
        "note": "No live Gold V1 observation snapshot supplied. No score is fabricated.",
    }


def run(input_path: Path, output_path: Path, summary_path: Path, allow_empty: bool) -> dict[str, Any]:
    config = load_config()
    validate_config(config)

    if input_path.exists():
        snapshot = json.loads(input_path.read_text(encoding="utf-8"))
        input_status = "OBSERVATION_SNAPSHOT_LOADED"
    elif allow_empty:
        snapshot = _empty_snapshot()
        input_status = "EMPTY_SHADOW_NO_LIVE_INPUT"
    else:
        raise FileNotFoundError(input_path)

    result = evaluate_snapshot(snapshot, config)
    result["input_status"] = input_status
    result["as_of"] = snapshot.get("as_of")
    result["source_snapshot"] = str(input_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Gold V1 – Comité décisionnel shadow",
        "",
        f"- Version: **{result['version']}**",
        f"- Statut: **{result['status']}**",
        f"- Mode décisionnel: **{result['decision_mode']}**",
        f"- Décisions shadow autorisées: **{'OUI' if result['shadow_decision_allowed'] else 'NON'}**",
        f"- Entrée données: **{input_status}**",
        f"- Régime: **{result['regime']}**",
        f"- GOLD_SCORE_MT: **{result['gold_score_mt']}**",
        f"- GOLD_SCORE_CT: **{result['gold_score_ct']}**",
        f"- ENTRY_SCORE: **{result['entry_score']}**",
        f"- QDS_OR: **{result['qds_or']}**",
        f"- Décision MT: **{result['decision_mt']}**",
        f"- Confiance: **{result['confidence']}**",
        "- Exécution réelle autorisée: **NON – RESEARCH_ONLY**",
        f"- Backtest bloque la décision shadow: **{'OUI' if result['backtest_blocks_shadow_decision'] else 'NON'}**",
        "",
        "La V1 est active pour les décisions de recherche shadow. Le backtest PIT/walk-forward reste requis pour valider les pondérations et toute promotion future hors shadow.",
        "T1/T2 sont exclus du module OR.",
    ]
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Gold V1 decisional shadow committee scoring")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args()

    result = run(args.input, args.output, args.summary, args.allow_empty)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
