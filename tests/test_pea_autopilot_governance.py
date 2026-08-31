from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_autopilot_v2_governance_is_fail_closed():
    cfg = json.loads((ROOT / "config" / "PEA_AUTOPILOT.json").read_text(encoding="utf-8"))
    gov = cfg["governance"]
    assert cfg["enabled"] is True
    assert cfg["wip_limit"] == 1
    assert gov["fail_closed"] is True
    assert gov["anti_lookahead_required"] is True
    assert gov["pit_required"] is True
    assert gov["allow_current_fundamentals_as_history"] is False
    assert gov["allow_future_returns_in_features"] is False
    assert gov["allow_weight_threshold_changes"] is False
    assert gov["allow_holdout_open"] is False
    assert gov["allow_real_orders"] is False
    assert gov["stale_run_remediation_forbidden"] is True
    assert gov["unknown_failure_autofix_forbidden"] is True


def test_autopilot_autofix_scope_is_explicit_and_bounded():
    cfg = json.loads((ROOT / "config" / "PEA_AUTOPILOT.json").read_text(encoding="utf-8"))
    assert cfg["max_transient_reruns"] <= 2
    assert 1 <= cfg["max_autofix_chain_depth"] <= 3
    assert cfg["autofix_workflows"] == ["V22.1 CI corrections + canonical 2010-2019 data"]
    assert cfg["deterministic_maintenance"] == {
        "v22/pit-mae-mfe-preopen": "scripts/maintenance_fix_v22_1_ci.py"
    }
    assert "config/" in cfg["forbidden_autofix_paths"]
    assert "data/" in cfg["forbidden_autofix_paths"]


def test_supervisor_avoids_expensive_target_install_for_report_only_runs():
    workflow = (ROOT / ".github" / "workflows" / "pea_autopilot_supervisor.yml").read_text(encoding="utf-8")
    assert "needs_target=true" in workflow
    assert 'if: steps.upstream.outputs.needs_target == \'true\'' in workflow
    assert "python -m pip install -e \"./target[test]\"" in workflow
    assert "cancel-in-progress: false" in workflow


def test_autopilot_has_stale_run_and_chain_guards():
    source = (ROOT / "scripts" / "pea_autopilot.py").read_text(encoding="utf-8")
    assert "STALE_RUN_SUPERSEDED" in source
    assert "AUTOFIX_CHAIN_LIMIT_REACHED" in source
    assert "AUTOFIX_FORBIDDEN_PATH_CHANGE" in source
    assert "git\", \"reset\", \"--hard\", \"HEAD" in source
    assert "RERUN_FAILED_JOBS" in source
    assert "validation_commands" in source
