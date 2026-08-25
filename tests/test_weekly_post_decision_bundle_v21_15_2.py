from __future__ import annotations

from pathlib import Path
import time

import pytest

from v182.reporting import weekly_post_decision_bundle_run as bundle


ROOT = Path(__file__).resolve().parents[1]


def _stub_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        bundle,
        "_persist_weekly_master_snapshot",
        lambda root: {"version": "WEEKLY_MASTER_SNAPSHOT_V1", "validated": True},
    )


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
    _stub_snapshot(monkeypatch)

    payload = bundle.run(tmp_path)

    assert starts["fund"] < ends["governance"]
    assert starts["governance"] < ends["fund"]
    assert payload["status"] == "SUCCESS_WEEKLY_POST_DECISION_BUNDLE"
    assert payload["decision_brief_remains_separate_required_gate"] is True
    assert payload["governance_overlaps_fund_flows"] is True
    assert payload["external_provider_concurrency_added"] is False
    assert payload["weekly_master_snapshot_required"] is True
    assert payload["workflow_failure_semantics_changed"] is False


def test_fund_flow_failure_remains_non_blocking_when_governance_succeeds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        bundle.etf_fund_flows_shadow_run,
        "run",
        lambda root: (_ for _ in ()).throw(RuntimeError("flow")),
    )
    monkeypatch.setattr(bundle.criteria_governance_audit, "run", lambda root: {"status": "SUCCESS"})
    _stub_snapshot(monkeypatch)

    payload = bundle.run(tmp_path)
    assert payload["status"] == "SUCCESS_WITH_FUND_FLOW_WARNING"
    assert payload["steps"]["etf_fund_flows_shadow"]["status"] == "FAILED"
    assert payload["steps"]["criteria_governance"]["status"] == "SUCCESS"
    assert payload["steps"]["weekly_master_snapshot"]["status"] == "SUCCESS"


def test_governance_failure_remains_blocking(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bundle.etf_fund_flows_shadow_run, "run", lambda root: {"status": "SUCCESS"})
    monkeypatch.setattr(
        bundle.criteria_governance_audit,
        "run",
        lambda root: (_ for _ in ()).throw(RuntimeError("governance")),
    )
    _stub_snapshot(monkeypatch)

    with pytest.raises(RuntimeError, match="CRITERIA_GOVERNANCE"):
        bundle.run(tmp_path)


def test_weekly_tail_runs_decision_brief_and_post_decision_as_required_parallel_gates() -> None:
    workflow = (ROOT / ".github" / "workflows" / "committee_master_daily.yml").read_text(encoding="utf-8")
    tail = (ROOT / "src" / "v182" / "reporting" / "weekly_tail_super_runner_v21_16_0.py").read_text(encoding="utf-8")

    assert "python -m v182.reporting.weekly_tail_super_runner_v21_16_0" in workflow
    assert "decision_brief" in tail
    assert "weekly_post_decision_bundle_run as weekly_post" in tail
    assert "ThreadPoolExecutor(max_workers=2, thread_name_prefix=\"weekly-finalize\")" in tail
    assert 'steps["decision_brief"] = brief' in tail
    assert 'steps["weekly_post_decision"] = weekly_post_result' in tail
    assert 'for name in ("decision_brief", "weekly_post_decision")' in tail
