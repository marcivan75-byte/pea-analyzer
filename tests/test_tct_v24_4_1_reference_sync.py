from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_release_manifest_paths_exist_and_excludes_runtime_state():
    manifest = json.loads((ROOT / "docs" / "TCT_RELEASE_MANIFEST_V24_4_1.json").read_text(encoding="utf-8"))
    paths: list[str] = []
    for group in manifest["python"].values():
        paths.extend(group)
    paths.extend(manifest["config"])
    paths.extend(manifest["workflows"])
    paths.extend(manifest["documentation"])
    paths.extend(manifest["tests_required"])
    missing = [path for path in paths if not (ROOT / path).exists()]
    assert not missing, missing
    assert manifest["package_policy"]["exclude_runtime_state_with_market_observations"] is True
    assert not any(path.startswith("state/") for path in paths)


def test_cdc_referential_and_addendum_are_v2441_and_preserve_governance():
    cdc = (ROOT / "docs" / "TCT_CDC_V24_4_1_FINAL.md").read_text(encoding="utf-8")
    ref = (ROOT / "docs" / "TCT_REFERENTIEL_V24_4_1_FINAL.md").read_text(encoding="utf-8")
    addendum = (ROOT / "docs" / "PROCESS_REFERENCE_V21_8_1_TCT_V24_4_1_ADDENDUM.md").read_text(encoding="utf-8")
    for text in (cdc, ref, addendum):
        assert "V24.4.1" in text
        assert "V21.8.1" in text
        assert "holdout" in text.lower()
        assert "T1/T2" in text
    assert "SUPERSEDED_FOR_NEW_SNAPSHOTS" in addendum
    assert "V24.4.1_ONLY_NO_MIX_WITH_V24.4.0" in ref
    assert "DATA_DEGRADED_SHADOW" in cdc


def test_v2441_configs_match_documented_coverage_and_epoch():
    cfg = json.loads((ROOT / "config" / "TCT_V24_4_1_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))
    gates = json.loads((ROOT / "config" / "TCT_V24_4_1_VALIDATION_GATES.json").read_text(encoding="utf-8"))
    assert cfg["thresholds"]["minimum_movement_coverage_for_scored_alert"] == 0.70
    assert cfg["thresholds"]["minimum_direction_coverage_for_directional_alert"] == 0.70
    assert cfg["pit_lineage"]["fingerprint_algorithm"] == "TCT_PIT_SHA256_CANONICAL_V2"
    assert gates["validation_epoch"] == "V24.4.1_ONLY_NO_MIX_WITH_V24.4.0"
    assert gates["governance"]["production_influence"] == 0.0


def test_active_catalyst_workflow_has_only_v2441_execution_steps():
    workflow = (ROOT / ".github" / "workflows" / "tct_next_session_context.yml").read_text(encoding="utf-8")
    run_lines = [line.strip() for line in workflow.splitlines() if line.strip().startswith("run: python -m v182.reporting.tct_")]
    assert "run: python -m v182.reporting.tct_next_session_catalyst_run_v24_4_1" in run_lines
    assert "run: python -m v182.reporting.tct_v24_4_1_pit_lineage" in run_lines
    assert "run: python -m v182.reporting.tct_v24_4_1_pit_validator_runtime" in run_lines
    assert "run: python -m v182.reporting.tct_next_session_catalyst_run" not in run_lines
    assert "run: python -m v182.reporting.tct_v24_4_pit_lineage" not in run_lines
    assert "run: python -m v182.reporting.tct_v24_4_pit_validator_runtime" not in run_lines
