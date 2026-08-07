from __future__ import annotations

from pathlib import Path
import json

from v182.decision.analyst_momentum import _committee_selection
from v182.io.frames import apply_observations, load_master, save_master
from v182.reporting.exports import export_master_excel
from v182.reporting.waves import wave8_scenarios

ROOT = Path(__file__).resolve().parents[3]


def ensure_committee_scenarios(root: Path | None = None) -> dict:
    """Ensure W08 scenarios exist for the same selection used by W09.

    Explicit COMMITTEE/WATCH rows remain authoritative. When none exist, the
    standard committee Top-300 score fallback is used. This is scenario analysis
    only; it never changes the execution gate.
    """
    root = root or ROOT
    outputs = root / "outputs"
    actions_path = outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    metrics_path = outputs / "audit" / "V18.2_SCENARIO_METRICS.json"

    actions = load_master(actions_path)
    selected, basis = _committee_selection(actions, limit=300)
    selected_isins = set(selected["isin"].dropna().astype(str)) if "isin" in selected.columns else set()
    observations = wave8_scenarios(actions, selected_isins)
    actions, quarantine = apply_observations(actions, observations)

    save_master(actions, actions_path)
    export_master_excel(
        actions,
        outputs / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx",
        "V18.2 Actions PEA actualisées",
    )

    observed_isins = {str(item.get("isin")) for item in observations if item.get("isin")}
    metrics = {
        "selection_basis": basis,
        "selected_rows": len(selected),
        "selected_isins": len(selected_isins),
        "scenario_isins": len(observed_isins),
        "scenario_observations": len(observations),
        "quarantined": len(quarantine),
        "execution_gate": "SHADOW_BLOCKED",
    }
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


def main() -> None:
    metrics = ensure_committee_scenarios()
    print(
        "WAVE_08B_SCENARIO_FALLBACK — "
        f"basis={metrics['selection_basis']} | selected={metrics['selected_rows']} | "
        f"scenario_isins={metrics['scenario_isins']} | observations={metrics['scenario_observations']} | "
        f"quarantine={metrics['quarantined']}"
    )


if __name__ == "__main__":
    main()
