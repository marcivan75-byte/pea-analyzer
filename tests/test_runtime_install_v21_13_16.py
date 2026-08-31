from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_weekly_workflow_uses_exact_runtime_requirements_without_editable_build():
    workflow = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    assert "PYTHONPATH: ${{ github.workspace }}/src" in workflow
    assert "cache-dependency-path: requirements-runtime.txt" in workflow
    assert "pip install --prefer-binary -r requirements-runtime.txt" in workflow
    assert "pip install -e ." not in workflow


def test_daily_and_weekly_share_same_production_runtime_dependency_contract():
    daily = (ROOT / ".github/workflows/committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    weekly = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    for workflow in (daily, weekly):
        assert "PYTHONPATH: ${{ github.workspace }}/src" in workflow
        assert "cache-dependency-path: requirements-runtime.txt" in workflow
        assert "pip install --prefer-binary -r requirements-runtime.txt" in workflow


def test_weekly_financial_execution_order_is_preserved_through_super_runners():
    workflow = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    tail = (ROOT / "src/v182/reporting/weekly_tail_super_runner_v21_16_0.py").read_text(encoding="utf-8")

    commands = [
        "python -m v182.audit.identity_hydration",
        "python -m v182.reporting.weekly_operational_runner_v4_4",
        "python -m v182.reporting.weekly_operational_runner_v4_4",
    ]
    positions = [workflow.index(command) for command in commands]
    assert positions == sorted(positions)
    assert all(workflow.count(command) == 1 for command in commands)

    ordered_tail_markers = [
        'steps["etf_structure_replay"]',
        'steps["friday_tactical_reuse"]',
        'steps["tactical_shadow"]',
        'steps["postmarket"]',
        "_brief_and_post_decision_parallel(root)",
    ]
    tail_positions = [tail.index(marker) for marker in ordered_tail_markers]
    assert tail_positions == sorted(tail_positions)
    assert "weekly_post_decision_bundle_run as weekly_post" in tail
    assert "decision_brief" in tail


def test_weekly_validation_and_state_persistence_remain_present():
    workflow = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    required = [
        "python -m compileall -q src",
        "python -m v182.audit.static_code_audit",
        "Save persistent OHLCV cache",
        "Save consolidated tactical decision state",
        "Save consolidated weekly research state",
        "Upload complete weekly Committee V4.4 results",
    ]
    for needle in required:
        assert needle in workflow
