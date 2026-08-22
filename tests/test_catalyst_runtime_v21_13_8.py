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
