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


def test_weekly_financial_execution_order_is_preserved():
    workflow = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    commands = [
        "python -m v182.audit.identity_hydration",
        "python -m v182.reporting.unified_runner",
        "python -m v182.reporting.etf_structure_state_replay",
        "python -m v182.reporting.daily_tct_ct_runner",
        "python -m v182.reporting.tactical_shadow_bundle_run",
        "python -m v182.reporting.tct_postmarket_bundle_run",
        "python -m v182.reporting.decision_brief",
        "python -m v182.reporting.etf_fund_flows_shadow_run",
        "python -m v182.reporting.criteria_governance_audit",
    ]
    positions = [workflow.index(command) for command in commands]
    assert positions == sorted(positions)
    assert all(workflow.count(command) == 1 for command in commands)


def test_weekly_validation_and_state_persistence_remain_present():
    workflow = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    required = [
        "python -m compileall -q src",
        "python -m v182.audit.static_code_audit",
        "Save persistent OHLCV cache",
        "Save consolidated tactical decision state",
        "Save consolidated weekly research state",
        "Upload complete weekly Committee V21.8.1 results",
    ]
    for needle in required:
        assert needle in workflow
