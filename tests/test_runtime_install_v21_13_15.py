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


def test_daily_financial_execution_order_is_preserved_inside_consolidated_runner():
    workflow = (ROOT / ".github/workflows/committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    facade = (ROOT / "src/v182/reporting/daily_consolidated_runner_v21_15_4.py").read_text(encoding="utf-8")
    base = (ROOT / "src/v182/reporting/daily_consolidated_runner_v21_15_5.py").read_text(encoding="utf-8")
    impl = (ROOT / "src/v182/reporting/daily_consolidated_runner_v21_15_7.py").read_text(encoding="utf-8")

    command = "python -m v182.reporting.daily_consolidated_runner_v21_15_4"
    assert workflow.count(command) == 1
    assert "daily_consolidated_runner_v21_15_7 as impl" in facade

    ordered_markers = [
        "collection_payload, local_optimizations = _run_collection_optimized_locals()",
        '"ETF_STRUCTURE_STATE_REPLAY"',
        "tactical_payload = tactical.run(root=root)",
    ]
    positions = [base.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)
    assert "daily_tactical_super_runner_v21_15_6 as tactical" in impl
    assert "ci_payload = daily_ci.run(root=root)" in impl
