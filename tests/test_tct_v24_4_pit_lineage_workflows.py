from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_daily_workflow_persists_close_ledger_from_existing_cache_only():
    workflow = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    assert "Persist compact daily closes for V24.4.1 PIT" in workflow
    assert "python -m v182.reporting.tct_pit_close_ledger_v24_4_1" in workflow
    assert "state/tct_context/TCT_DAILY_CLOSE_LEDGER.csv" in workflow
    step = workflow.split("- name: Persist compact daily closes for V24.4.1 PIT", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error: true" in step


def test_catalyst_workflow_applies_lineage_before_validator():
    workflow = (ROOT / ".github" / "workflows" / "tct_next_session_context.yml").read_text(encoding="utf-8")
    lineage_pos = workflow.index("Apply fail-closed V24.4.1 PIT lineage")
    validator_pos = workflow.index("Validate accumulated V24.4.1 PIT ledger")
    assert lineage_pos < validator_pos
    assert "python -m v182.reporting.tct_v24_4_1_pit_lineage" in workflow
    assert "TCT_V24_4_1_PIT_LINEAGE_AUDIT.json" in workflow


def test_lineage_config_forbids_synthetic_historical_replay_and_requires_quality():
    cfg = json.loads((ROOT / "config" / "TCT_V24_4_1_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))
    lineage = cfg["pit_lineage"]
    assert lineage["first_subsequent_close_only"] is True
    assert lineage["minimum_snapshot_outcome_coverage"] >= 0.80
    assert lineage["snapshot_fingerprint_required"] is True
    assert lineage["fingerprint_algorithm"] == "TCT_PIT_SHA256_CANONICAL_V2"
    assert lineage["fail_closed_on_fingerprint_mismatch"] is True
    assert lineage["historical_replay_without_historical_tct_snapshots_forbidden"] is True
    governance = cfg["governance"]
    assert governance["decision_influence"] == 0.0
    assert governance["score_influence"] == 0.0
    assert governance["sizing_influence"] == 0.0
    assert governance["stop_loss_influence"] == 0.0
    assert governance["ct_influence"] == 0.0
    assert governance["promotion_authority"] is False
