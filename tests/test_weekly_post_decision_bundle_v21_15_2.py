from __future__ import annotations

from pathlib import Path
import time

import pytest

from v182.reporting import weekly_post_decision_bundle_run as bundle


def test_weekly_bundle_overlaps_fund_flows_and_governance_after_brief(tmp_path, monkeypatch) -> None:
    calls: list[str] = []
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}

    def brief(root: Path):
        calls.append("brief")
        starts["brief"] = time.perf_counter()
        time.sleep(0.01)
        ends["brief"] = time.perf_counter()
        return {"status": "SUCCESS"}

    def fund(root: Path):
        calls.append("fund")
        starts["fund"] = time.perf_counter()
        time.sleep(0.05)
        ends["fund"] = time.perf_counter()
        return {"status": "SUCCESS"}

    def governance(root: Path):
        calls.append("governance")
        starts["governance"] = time.perf_counter()
        time.sleep(0.05)
        ends["governance"] = time.perf_counter()
        return {"status": "SUCCESS"}

    monkeypatch.setattr(bundle.decision_brief, "run", brief)
    monkeypatch.setattr(bundle.etf_fund_flows_shadow_run, "run", fund)
    monkeypatch.setattr(bundle.criteria_governance_audit, "run", governance)

    payload = bundle.run(tmp_path)

    assert calls[0] == "brief"
    assert starts["fund"] >= ends["brief"]
    assert starts["governance"] >= ends["brief"]
    assert starts["fund"] < ends["governance"]
    assert starts["governance"] < ends["fund"]
    assert payload["status"] == "SUCCESS_WEEKLY_POST_DECISION_BUNDLE"
    assert payload["governance_overlaps_fund_flows"] is True
    assert payload["external_provider_concurrency_added"] is False
    assert payload["interpreter_startups_avoided"] == 2


def test_decision_brief_failure_prevents_later_steps(tmp_path, monkeypatch) -> None:
    later_calls: list[str] = []

    def broken_brief(root: Path):
        raise RuntimeError("brief failed")

    monkeypatch.setattr(bundle.decision_brief, "run", broken_brief)
    monkeypatch.setattr(bundle.etf_fund_flows_shadow_run, "run", lambda root: later_calls.append("fund"))
    monkeypatch.setattr(bundle.criteria_governance_audit, "run", lambda root: later_calls.append("governance"))

    with pytest.raises(RuntimeError, match="DECISION_BRIEF"):
        bundle.run(tmp_path)
    assert later_calls == []


def test_fund_flow_failure_remains_non_blocking_when_governance_succeeds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bundle.decision_brief, "run", lambda root: {"status": "SUCCESS"})
    monkeypatch.setattr(bundle.etf_fund_flows_shadow_run, "run", lambda root: (_ for _ in ()).throw(RuntimeError("flow")))
    monkeypatch.setattr(bundle.criteria_governance_audit, "run", lambda root: {"status": "SUCCESS"})

    payload = bundle.run(tmp_path)
    assert payload["status"] == "SUCCESS_WITH_FUND_FLOW_WARNING"
    assert payload["steps"]["etf_fund_flows_shadow"]["status"] == "FAILED"
    assert payload["steps"]["criteria_governance"]["status"] == "SUCCESS"


def test_governance_failure_remains_blocking(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bundle.decision_brief, "run", lambda root: {"status": "SUCCESS"})
    monkeypatch.setattr(bundle.etf_fund_flows_shadow_run, "run", lambda root: {"status": "SUCCESS"})
    monkeypatch.setattr(bundle.criteria_governance_audit, "run", lambda root: (_ for _ in ()).throw(RuntimeError("governance")))

    with pytest.raises(RuntimeError, match="CRITERIA_GOVERNANCE"):
        bundle.run(tmp_path)
