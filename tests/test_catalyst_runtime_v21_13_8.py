from __future__ import annotations

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_preopen_and_postmarket_use_the_same_tct_ct_preselection_scope():
    cfg = json.loads((ROOT / "config" / "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))
    scope = cfg["candidate_selection"]["preselection_scope"]
    assert scope["enabled"] is True
    assert set(scope["applies_to_phases"]) == {"PREOPEN", "POSTMARKET"}
    assert scope["asset_class"] == "ACTION"
    assert scope["tct_top_n"] == 20
    assert scope["action_ct_top_n"] == 20
    assert scope["union_max"] == 40
    assert scope["deduplicate_by"] == "isin"
    assert scope["fail_closed_if_marker_missing"] is True


def test_autonomous_preopen_uses_minimal_dependency_profile():
    workflow = (ROOT / ".github" / "workflows" / "tct_next_session_context.yml").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements-catalyst.txt").read_text(encoding="utf-8").lower()

    assert "requirements-catalyst.txt" in workflow
    assert "PYTHONPATH: ${{ github.workspace }}/src" in workflow
    assert "pip install -e ." not in workflow
    assert "pip install --prefer-binary -r requirements-catalyst.txt" in workflow

    for required in ("numpy", "pandas", "requests", "yfinance"):
        assert required in requirements
    for forbidden in ("playwright", "pyarrow", "openpyxl", "pypdf", "lxml", "ta>="):
        assert forbidden not in requirements


def test_only_preopen_remains_scheduled_as_autonomous_catalyst_job():
    workflow = (ROOT / ".github" / "workflows" / "tct_next_session_context.yml").read_text(encoding="utf-8")
    assert workflow.count("cron:") == 1
    assert 'cron: "40 6 * * 1-5"' in workflow
    assert 'cron: "15 21 * * 1-5"' not in workflow


def test_daily_runtime_consolidates_decision_state_without_mixing_ohlcv_cache():
    workflow = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")

    assert "Restore consolidated tactical decision state" in workflow
    assert "Save consolidated tactical decision state" in workflow
    assert "decision-state-v1-${{ github.run_id }}" in workflow
    assert "steps.decision-state.outputs.cache-matched-key == ''" in workflow
    for state_path in (
        "state/TCT_V24_1_7_T1_STATE.json",
        "state/tct_context/",
        "state/action_ct/",
        "state/action_ct_v22_1/",
        "state/provenance/",
    ):
        assert state_path in workflow

    # OHLCV keeps its independent, success-only anti-poisoning cache policy.
    assert "Save persistent OHLCV cache" in workflow
    assert "success() && hashFiles('data/cache/**') != ''" in workflow
    assert "key: ohlcv-v3-${{ github.run_id }}" in workflow
    assert "compression-level: 1" in workflow


def test_weekly_runtime_uses_two_state_caches_and_preserves_migration_fallbacks():
    workflow = (ROOT / ".github" / "workflows" / "committee_master_daily.yml").read_text(encoding="utf-8")

    assert "Restore consolidated tactical decision state" in workflow
    assert "Restore consolidated weekly research state" in workflow
    assert "Save consolidated tactical decision state" in workflow
    assert "Save consolidated weekly research state" in workflow
    assert "decision-state-v1-${{ github.run_id }}" in workflow
    assert "weekly-research-state-v1-${{ github.run_id }}" in workflow
    assert "steps.decision-state.outputs.cache-matched-key == ''" in workflow
    assert "steps.weekly-research-state.outputs.cache-matched-key == ''" in workflow
    assert "state/sector_rotation_v2/" in workflow
    assert "state/etf_fund_flows/" in workflow

    # No decision, scoring or validation capability is removed by the runtime optimization.
    for required_step in (
        "Run weekly unified Committee pipeline",
        "Friday TCT CT scoring and V21.8",
        "Action CT V22.0 + V22.1 Friday shared-history SHADOW",
        "Run consolidated Friday POSTMARKET catalyst snapshot V24.4.2",
        "Validate Friday POSTMARKET V24.4.2 PIT ledger",
        "Run ETF Fund Flows V1 SHADOW context",
        "Audit criteria governance",
    ):
        assert required_step in workflow
    assert workflow.count("python -m v182.reporting.action_ct_shadow_bundle_run") == 1
    assert "state/action_ct/" in workflow
    assert "state/action_ct_v22_1/" in workflow
    assert "compression-level: 1" in workflow
