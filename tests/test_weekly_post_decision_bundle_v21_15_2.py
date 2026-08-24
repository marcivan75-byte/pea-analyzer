from __future__ import annotations

from pathlib import Path
import time

import pytest

from v182.reporting import weekly_post_decision_bundle_run as bundle


ROOT = Path(__file__).resolve().parents[1]


def test_weekly_bundle_overlaps_fund_flows_and_governance(tmp_path, monkeypatch) -> None:
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    def fund(root: Path):
        starts["fund"] = time.perf_counter()
        time.sleep(0.05)
        ends["fund"] = time.perf_counter()
        return {"status": "SUCCESS"}

    def governance(root: Path):
        starts["governance"] = time.perf_counter()
        time.sleep(0.05)
        ends["governance"] = time.perf_counter()
        return {"status": "SUCCESS"}

    monkeypatch.setattr(bundle.etf_fund_flows_shadow_run, "run", fund)
    monkeypatch.setattr(bundle.criteria_governance_audit, "run", governance)

    payload = bundle.run(tmp_path)

    assert starts["fund"] < ends["governance"]
    assert starts["governance"] < ends["fund"]
    assert payload["status"] == "SUCCESS_WEEKLY_POST_DECISION_BUNDLE"
    assert payload["decision_brief_remains_separate_required_gate"] is True
    assert payload["governance_overlaps_fund_flows"] is True
    assert payload["external_provider_concurrency_added"] is False
    assert payload["interpreter_startups_avoided"] == 1
    assert payload["workflow_failure_semantics_changed"] is False


def test_fund_flow_failure_remains_non_blocking_when_governance_succeeds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        bundle.etf_fund_flows_shadow_run,
        "run",
        lambda root: (_ for _ in ()).throw(RuntimeError("flow")),
    )
    monkeypatch.setattr(bundle.criteria_governance_audit, "run", lambda root: {"status": "SUCCESS"})

    payload = bundle.run(tmp_path)
    assert payload["status"] == "SUCCESS_WITH_FUND_FLOW_WARNING"
    assert payload["steps"]["etf_fund_flows_shadow"]["status"] == "FAILED"
    assert payload["steps"]["criteria_governance"]["status"] == "SUCCESS"


def test_governance_failure_remains_blocking(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bundle.etf_fund_flows_shadow_run, "run", lambda root: {"status": "SUCCESS"})
    monkeypatch.setattr(
        bundle.criteria_governance_audit,
        "run",
        lambda root: (_ for _ in ()).throw(RuntimeError("governance")),
    )

    with pytest.raises(RuntimeError, match="CRITERIA_GOVERNANCE"):
        bundle.run(tmp_path)


def test_weekly_workflow_keeps_decision_brief_always_gate_before_default_success_bundle() -> None:
    workflow = (ROOT / ".github" / "workflows" / "committee_master_daily.yml").read_text(encoding="utf-8")
    brief_block = (
        "      - name: Build synthetic decision brief\n"
        "        if: always()\n"
        "        run: python -m v182.reporting.decision_brief\n"
    )
    bundle_block = (
        "      - name: Run Fund Flows + criteria governance bundle\n"
        "        run: python -m v182.reporting.weekly_post_decision_bundle_run\n"
    )
    assert brief_block in workflow
    assert bundle_block in workflow
    assert workflow.index(brief_block) < workflow.index(bundle_block)
    assert "if: always()\n        run: python -m v182.reporting.weekly_post_decision_bundle_run" not in workflow
