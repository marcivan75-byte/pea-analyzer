from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_final_manifest_matches_validated_runtime_scope():
    payload = json.loads((ROOT / "FINAL_PACKAGE_V21_13_16_MANIFEST.json").read_text(encoding="utf-8"))
    assert payload["package_name"] == "PEA_ANALYZER_FINAL_V21_13_16"
    assert payload["package_version"] == "V21.13.16"
    assert payload["status"] == "FINAL_VALIDATED_RUNTIME_PACKAGE"
    assert payload["active_scope"]["actions"] == ["TCT", "CT", "MT"]
    assert payload["active_scope"]["etf"] == ["CT", "MT"]
    assert payload["governance"]["t1_t2_scope"] == "ACTION_TCT_ONLY"
    assert payload["governance"]["production_gdelt_grouping_enabled"] is False
    assert payload["governance"]["real_orders_enabled"] is False
    assert all(value == "SUCCESS" for value in payload["final_validation"].values())


def test_final_runtime_dependency_contract_is_exact():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected = [str(value).strip() for value in project["project"]["dependencies"]]
    runtime = [
        line.strip()
        for line in (ROOT / "requirements-runtime.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert runtime == expected
    assert len(runtime) == 15


def test_final_package_workflow_is_publish_only_and_does_not_modify_production_workflows():
    workflow = (ROOT / ".github/workflows/publish_final_package_v21_13_16.yml").read_text(encoding="utf-8")
    assert "git archive" in workflow
    assert "sha256sum PEA_ANALYZER_FINAL_V21_13_16.zip" in workflow
    assert "actions/upload-artifact@" in workflow
    assert "retention-days: 90" in workflow
    assert "python -m v182.reporting" not in workflow
    assert "FINNHUB_API_KEY" not in workflow
    assert "MARKETSTACK_API_KEY" not in workflow
    assert "ALPHA_VANTAGE_API_KEY" not in workflow
