from __future__ import annotations

import json
from pathlib import Path

from v182.decision.etf_mt_fiche_validator import (
    DRAFT,
    INCOMPLETE,
    READY,
    merge_human_fields,
    validate_fiche,
)
from v182.reporting.etf_mt_process_annotate import build_instruction_fiche, write_instruction_fiches


def _config() -> dict:
    return json.loads(Path("config/ETF_MT_PROCESS_V21.json").read_text(encoding="utf-8"))


def _base() -> dict:
    return {
        "instrument_id": "IE00ELIG",
        "name": "World Core",
        "score_final": 88.4,
        "decision": "BUY_CANDIDATE",
        "v21_gate_status": "PASS",
        "v21_gate_reasons": "",
        "v21_gate_warnings": "",
        "fund_total_assets_eur_m": 900.0,
        "ter_pct": 0.12,
        "category": "WORLD",
        "staleness_days": 1,
    }


def test_empty_fiche_stays_draft():
    fiche = build_instruction_fiche(_base(), _config())
    validated = validate_fiche(fiche)
    assert validated["status"] == DRAFT
    assert "THESIS_TOO_SHORT" in validated["validation_missing"]
    assert validated["live_orders_enabled"] is False


def test_partial_fiche_is_incomplete():
    fiche = build_instruction_fiche(_base(), _config())
    fiche["to_complete"]["thesis_8_12_lines"] = "x" * 50
    validated = validate_fiche(fiche)
    assert validated["status"] == INCOMPLETE
    assert "INVALIDATION_TOO_SHORT" in validated["validation_missing"]
    assert validated["status"] != READY


def test_complete_fiche_ready_for_review_not_live():
    fiche = build_instruction_fiche(_base(), _config())
    fiche["to_complete"].update(
        {
            "job_horizon": "36 mois",
            "thesis_8_12_lines": "Exposition Europe value cyclique sur 36 mois avec catalyseur benefices.",
            "invalidation": "Sortie si PMI < 48 deux mois consécutifs.",
            "peers_rejected": "ISIN1; ISIN2; ISIN3",
        }
    )
    validated = validate_fiche(fiche)
    assert validated["status"] == READY
    assert validated["validation_missing"] == []
    assert validated["live_orders_enabled"] is False
    assert validated["promotion_allowed"] is False


def test_horizon_outside_band_rejected():
    fiche = build_instruction_fiche(_base(), _config())
    fiche["to_complete"].update(
        {
            "job_horizon": "8 mois",
            "thesis_8_12_lines": "x" * 50,
            "invalidation": "y" * 25,
            "peers_rejected": "A, B, C",
        }
    )
    validated = validate_fiche(fiche)
    assert validated["status"] == INCOMPLETE
    assert "HORIZON_OUT_OF_18_60" in validated["validation_missing"]


def test_rerun_preserves_human_thesis(tmp_path: Path):
    cfg = _config()
    import pandas as pd

    annotated = pd.DataFrame(
        [
            {
                **_base(),
                "selected": True,
                "v21_thesis_eligible": "YES",
            }
        ]
    )
    first = write_instruction_fiches(annotated, tmp_path, cfg)
    dest = Path(first["outputs"]["fiches_dir"]) / "IE00ELIG.json"
    payload = json.loads(dest.read_text(encoding="utf-8"))
    payload["to_complete"]["thesis_8_12_lines"] = "Thèse humaine déjà rédigée sur le cycle européen et les banques."
    payload["to_complete"]["invalidation"] = "Invalidation humaine PMI et banks."
    payload["to_complete"]["peers_rejected"] = "AAA, BBB, CCC"
    payload["to_complete"]["job_horizon"] = "24 mois"
    dest.write_text(json.dumps(payload), encoding="utf-8")

    second = write_instruction_fiches(annotated, tmp_path, cfg)
    preserved = json.loads(dest.read_text(encoding="utf-8"))
    assert "Thèse humaine" in preserved["to_complete"]["thesis_8_12_lines"]
    assert preserved["status"] == READY
    assert second["fiches_written"] == 1


def test_merge_does_not_overwrite_filled_fields():
    generated = build_instruction_fiche(_base(), _config())
    existing = build_instruction_fiche(_base(), _config())
    existing["to_complete"]["thesis_8_12_lines"] = "Texte déjà saisi par l'opérateur pour la thèse moyen terme."
    merged = merge_human_fields(generated, existing)
    assert "opérateur" in merged["to_complete"]["thesis_8_12_lines"]
