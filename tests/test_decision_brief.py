import csv
import json
from pathlib import Path

import pandas as pd
from docx import Document

from v182.reporting.decision_brief import run


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _write_decisions(path: Path) -> None:
    _write_csv(
        path,
        [
            {"asset_class": "ACTION", "horizon": "CT", "isin": "FR1", "name": "Alpha", "decision": "WATCH", "score": "72.4", "coverage_pct": "91.0"},
            {"asset_class": "ETF", "horizon": "MT", "isin": "FR2", "name": "Beta", "decision": "BUY_CANDIDATE", "score": "81.2", "coverage_pct": "95.0"},
        ],
    )


def _write_reference(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"asset_class": "ACTION", "horizon": "CT", "name": "Alpha", "isin": "FR1", "decision": "WATCH", "final_score": 72.4, "criterion": "momentum", "criterion_status": "ACTIVE", "raw_value": 1.2, "direction": "HIGH", "criterion_score_0_100": 88.0, "theoretical_weight_pct": 40.0, "effective_weight_pct": 40.0, "weighted_contribution_points": 35.2, "source": "source-a", "as_of": "2026-08-22", "evidence_level": "A", "validation_status": "VALID"},
        {"asset_class": "ACTION", "horizon": "CT", "name": "Alpha", "isin": "FR1", "decision": "WATCH", "final_score": 72.4, "criterion": "consensus_delta_4w", "criterion_status": "ACTIVE", "raw_value": 4.0, "direction": "HIGH", "criterion_score_0_100": 80.0, "theoretical_weight_pct": 30.0, "effective_weight_pct": 30.0, "weighted_contribution_points": 24.0, "source": "source-b", "as_of": "2026-08-22", "evidence_level": "A", "validation_status": "VALID"},
        {"asset_class": "ACTION", "horizon": "CT", "name": "Alpha", "isin": "FR1", "decision": "WATCH", "final_score": 72.4, "criterion": "target_upside_pct_v21", "criterion_status": "ACTIVE", "raw_value": 14.0, "direction": "HIGH", "criterion_score_0_100": 44.0, "theoretical_weight_pct": 30.0, "effective_weight_pct": 30.0, "weighted_contribution_points": 13.2, "source": "source-c", "as_of": "2026-08-22", "evidence_level": "B", "validation_status": "VALID"},
        {"asset_class": "ETF", "horizon": "MT", "name": "Beta", "isin": "FR2", "decision": "BUY_CANDIDATE", "final_score": 81.2, "criterion": "trend", "criterion_status": "ACTIVE", "raw_value": 2.0, "direction": "HIGH", "criterion_score_0_100": 92.0, "theoretical_weight_pct": 50.0, "effective_weight_pct": 50.0, "weighted_contribution_points": 46.0, "source": "source-d", "as_of": "2026-08-22", "evidence_level": "A", "validation_status": "VALID"},
        {"asset_class": "ETF", "horizon": "MT", "name": "Beta", "isin": "FR2", "decision": "BUY_CANDIDATE", "final_score": 81.2, "criterion": "breadth", "criterion_status": "ACTIVE", "raw_value": 1.0, "direction": "HIGH", "criterion_score_0_100": 72.4, "theoretical_weight_pct": 50.0, "effective_weight_pct": 50.0, "weighted_contribution_points": 36.2, "source": "source-e", "as_of": "2026-08-22", "evidence_level": "A", "validation_status": "VALID"},
    ]
    pd.DataFrame(rows).to_excel(path, sheet_name="Referentiel_pondere", index=False)


def test_successful_run_produces_decision_driven_v3_brief(tmp_path: Path):
    _write_json(
        tmp_path / "outputs/unified/UNIFIED_SUMMARY_LATEST.json",
        {"status": "SUCCESS", "run_id": "run-1", "steps": {"committee": {"status": "SUCCESS"}, "ci_explainability": {"status": "SUCCESS"}}},
    )
    _write_json(
        tmp_path / "outputs/audit/CI_EXPLAINABILITY_AUDIT.json",
        {"reconstruction": {"within_0_02_points": True}},
    )
    _write_decisions(tmp_path / "outputs/committee_master/COMMITTEE_DECISIONS.csv")
    _write_reference(tmp_path / "outputs/committee_master/CI_REFERENTIEL_PONDERE.xlsx")
    _write_csv(
        tmp_path / "outputs/committee_master/V21_8_ENTRY_EXIT_CHALLENGER.csv",
        [
            {"asset_class": "ACTION", "horizon": "CT", "isin": "FR1", "v21_8_entry_state": "WAIT_CONFIRMATION", "v21_8_position_state": "HOLD", "v21_8_entry_reasons": "confirmation requise", "v21_8_position_reasons": "stable"},
            {"asset_class": "ETF", "horizon": "MT", "isin": "FR2", "v21_8_entry_state": "ENTRY_READY", "v21_8_position_state": "HOLD", "v21_8_entry_reasons": "conditions réunies", "v21_8_position_reasons": "stable"},
        ],
    )
    _write_csv(
        tmp_path / "outputs/daily_tct_ct/TCT_NEXT_SESSION_CATALYST_V24_4_2.csv",
        [{"isin": "FR1", "catalyst_state": "UP_CATALYST_SHADOW", "movement_potential_score": "78", "direction_bias_score": "31", "news_technical_conflict": "false", "exit_state": "", "data_quality_state": "COMPLETE_ENOUGH", "news_event_types": "earnings"}],
    )
    _write_csv(
        tmp_path / "state/provenance/CI_DECISION_SNAPSHOT.csv",
        [
            {"generated_at_utc": "2026-08-15T20:00:00+00:00", "asset_class": "ACTION", "horizon": "CT", "isin": "FR1", "name": "Alpha", "decision": "REVIEW", "score": "68.0", "coverage_pct": "90.0", "consensus_score": "70", "consensus_delta_4w": "1", "target_upside_pct": "10", "entry_state": "WAIT", "position_state": "HOLD", "catalyst_state": "", "data_confidence": "MOYENNE"},
            {"generated_at_utc": "2026-08-15T20:00:00+00:00", "asset_class": "ACTION", "horizon": "MT", "isin": "FR3", "name": "Gamma", "decision": "WATCH", "score": "71.0", "coverage_pct": "92.0", "consensus_score": "70", "consensus_delta_4w": "0", "target_upside_pct": "8", "entry_state": "WAIT", "position_state": "HOLD", "catalyst_state": "", "data_confidence": "FORTE"},
        ],
    )

    payload = run(tmp_path)

    assert payload["version"] == "CI_DECISION_BRIEF_V3"
    assert payload["decision_status"] == "READY_FOR_REVIEW"
    assert payload["selected_count"] == 2
    assert payload["score_or_decision_mutation"] is False
    assert payload["external_collection_calls"] == 0
    alpha = next(row for row in payload["decision_rows"] if row["isin"] == "FR1")
    beta = next(row for row in payload["decision_rows"] if row["isin"] == "FR2")
    assert alpha["change_state"] == "DECISION_MODIFIEE"
    assert round(alpha["score_delta"], 1) == 4.4
    assert alpha["catalyst_state"] == "UP_CATALYST_SHADOW"
    assert beta["action_bucket"] == "ACTION IMMEDIATE"
    assert beta["data_confidence"] == "FORTE"
    assert payload["removed_from_selection"][0]["isin"] == "FR3"
    assert (tmp_path / "outputs/decision_brief/DECISION_BRIEF.md").exists()
    assert (tmp_path / "outputs/decision_brief/CI_DECISION_MATRIX_V3.csv").exists()
    docx_path = tmp_path / "outputs/decision_brief/CI_DECISION_BRIEF_V3.docx"
    assert docx_path.exists()
    text = "\n".join(paragraph.text for paragraph in Document(docx_path).paragraphs)
    assert "Décisions à prendre" in text
    assert "Changements depuis S-1" in text
    snapshot = pd.read_csv(tmp_path / "state/provenance/CI_DECISION_SNAPSHOT.csv", sep=";")
    assert set(snapshot["isin"]) == {"FR1", "FR2"}


def test_failed_pipeline_is_blocked_but_still_publishes_brief(tmp_path: Path):
    _write_json(
        tmp_path / "outputs/unified/UNIFIED_SUMMARY_LATEST.json",
        {"status": "PARTIAL_SUCCESS", "run_id": "run-2", "steps": {"refresh": {"status": "FAILED"}, "committee": {"status": "SKIPPED_DEPENDENCY"}}},
    )

    payload = run(tmp_path)

    assert payload["decision_status"] == "BLOCKED"
    assert payload["failed_steps"] == ["refresh"]
    assert payload["skipped_dependencies"] == ["committee"]
    assert payload["score_or_decision_mutation"] is False
    assert payload["selected_count"] == 0
    assert (tmp_path / "outputs/decision_brief/DECISION_BRIEF.json").exists()
    assert (tmp_path / "outputs/decision_brief/CI_DECISION_BRIEF_V3.docx").exists()
