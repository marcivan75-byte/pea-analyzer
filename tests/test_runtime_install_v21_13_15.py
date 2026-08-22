from __future__ import annotations

from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def _runtime_requirements() -> list[str]:
    lines = (ROOT / "requirements-runtime.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def test_runtime_requirements_match_project_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected = [str(item).strip() for item in project["project"]["dependencies"]]
    assert _runtime_requirements() == expected
    assert len(expected) == 13


def test_daily_workflow_uses_runtime_file_and_pythonpath():
    workflow = (ROOT / ".github/workflows/committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    assert "PYTHONPATH: ${{ github.workspace }}/src" in workflow
    assert "cache-dependency-path: requirements-runtime.txt" in workflow
    assert "requirements-runtime.txt" in workflow
    assert "Install production runtime" in workflow


def test_daily_financial_execution_order_is_preserved():
    workflow = (ROOT / ".github/workflows/committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    commands = [
        "python -m v182.reporting.run",
        "python -m v182.reporting.etf_structure_state_replay",
        "python -m v182.reporting.daily_tct_ct_runner",
        "python -m v182.reporting.tactical_shadow_bundle_run",
        "python -m v182.reporting.tct_postmarket_bundle_run",
    ]
    positions = [workflow.index(command) for command in commands]
    assert positions == sorted(positions)
    assert all(workflow.count(command) == 1 for command in commands)
