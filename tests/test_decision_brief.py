import csv
import json
from pathlib import Path

from v182.reporting.decision_brief import run


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_decisions(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["asset_class", "horizon", "isin", "name", "decision", "score", "coverage_pct"], delimiter=";")
        writer.writeheader()
        writer.writerows([
            {"asset_class": "ACTION", "horizon": "CT", "isin": "FR1", "name": "Alpha", "decision": "WATCH", "score": "72.4", "coverage_pct": "91.0"},
            {"asset_class": "ETF", "horizon": "MT", "isin": "FR2", "name": "Beta", "decision": "BUY_CANDIDATE", "score": "81.2", "coverage_pct": "95.0"},
        ])


def test_successful_run_produces_ready_decision_brief(tmp_path: Path):
    _write_json(tmp_path / "outputs/unified/UNIFIED_SUMMARY_LATEST.json", {"status": "SUCCESS", "run_id": "run-1", "steps": {"committee": {"status": "SUCCESS"}}})
    _write_json(tmp_path / "outputs/audit/CI_EXPLAINABILITY_AUDIT.json", {"reconstruction": {"within_0_02_points": True}})
    _write_decisions(tmp_path / "outputs/committee_master/COMMITTEE_DECISIONS.csv")

    payload = run(tmp_path)

    assert payload["decision_status"] == "READY_FOR_REVIEW"
    assert payload["selected_count"] == 2
    assert payload["top_candidates"][0]["name"] == "Beta"
    assert payload["real_orders_enabled"] is False
    assert (tmp_path / "outputs/decision_brief/DECISION_BRIEF.md").exists()


def test_failed_pipeline_is_blocked_but_still_publishes_brief(tmp_path: Path):
    _write_json(tmp_path / "outputs/unified/UNIFIED_SUMMARY_LATEST.json", {"status": "PARTIAL_SUCCESS", "run_id": "run-2", "steps": {"refresh": {"status": "FAILED"}, "committee": {"status": "SKIPPED_DEPENDENCY"}}})

    payload = run(tmp_path)

    assert payload["decision_status"] == "BLOCKED"
    assert payload["failed_steps"] == ["refresh"]
    assert payload["skipped_dependencies"] == ["committee"]
    assert payload["score_or_decision_mutation"] is False
    assert (tmp_path / "outputs/decision_brief/DECISION_BRIEF.json").exists()

