from __future__ import annotations

import json

import pytest

from v182.reporting import weekly_tail_super_runner_v21_16_0 as weekly


def _ok(status: str = "SUCCESS") -> dict:
    return {"status": status}


def test_weekly_tail_reuses_friday_and_releases_postmarket_before_action_join(monkeypatch, tmp_path):
    order: list[str] = []

    monkeypatch.setattr(weekly.etf_replay, "run", lambda root: order.append("replay") or _ok())
    monkeypatch.setattr(weekly.friday_reuse, "run", lambda root: order.append("reuse") or _ok())

    def fake_postmarket(root):
        order.append("postmarket")
        return _ok("SUCCESS_POSTMARKET_SINGLE_PROCESS")

    def fake_tactical(root, *, tct_complete_callback=None):
        order.append("tactical_start")
        assert tct_complete_callback is not None
        tct_complete_callback({"status": "SUCCESS", "rows": 3}, None)
        order.append("action_ct_join")
        return _ok("SUCCESS_TACTICAL_PARALLEL_SHARED_RUNTIME")

    monkeypatch.setattr(weekly.postmarket, "run", fake_postmarket)
    monkeypatch.setattr(weekly.tactical, "run", fake_tactical)
    monkeypatch.setattr(weekly.decision_brief, "run", lambda root: order.append("brief") or _ok())
    monkeypatch.setattr(weekly.weekly_post, "run", lambda root: order.append("weekly_post") or _ok())

    payload = weekly.run(root=tmp_path)

    assert payload["status"] == "SUCCESS_WEEKLY_TAIL_OPTIMIZED"
    assert order.index("reuse") < order.index("tactical_start")
    assert order.index("postmarket") < order.index("action_ct_join")
    contract = payload["optimization_contract"]
    assert contract["friday_committee_score_recompute_removed"] is True
    assert contract["friday_tct_baseline_recompute_removed"] is True
    assert contract["friday_v21_8_second_application_removed"] is True
    assert contract["postmarket_overlapped_after_tct_completion"] is True
    assert contract["decision_brief_overlaps_weekly_post_decision"] is True
    assert payload["decision_logic_changed"] is False
    assert payload["criteria_changed"] is False
    assert payload["weights_changed"] is False
    assert payload["thresholds_changed"] is False
    assert payload["real_orders_enabled"] is False

    audit = json.loads((tmp_path / "outputs/audit/WEEKLY_TAIL_SUPER_RUNTIME_V21_16_0.json").read_text())
    assert audit["version"] == weekly.VERSION


def test_weekly_tail_attempts_postmarket_when_tactical_fails_before_callback(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(weekly.etf_replay, "run", lambda root: _ok())
    monkeypatch.setattr(weekly.friday_reuse, "run", lambda root: _ok())

    def failed_tactical(root, *, tct_complete_callback=None):
        calls.append("tactical")
        raise RuntimeError("TACTICAL_FAILURE")

    monkeypatch.setattr(weekly.tactical, "run", failed_tactical)
    monkeypatch.setattr(weekly.postmarket, "run", lambda root: calls.append("postmarket") or _ok())
    monkeypatch.setattr(weekly.decision_brief, "run", lambda root: _ok())
    monkeypatch.setattr(weekly.weekly_post, "run", lambda root: _ok())

    payload = weekly.run(root=tmp_path)

    assert calls == ["tactical", "postmarket"]
    assert payload["status"] == "SUCCESS_WEEKLY_TAIL_WITH_ADVISORY_WARNINGS"
    assert "tactical_shadow" in payload["advisory_failures"]
    assert payload["steps"]["postmarket"]["status"] == "SUCCESS"


def test_weekly_tail_fails_closed_when_required_friday_reuse_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(weekly.etf_replay, "run", lambda root: _ok())
    monkeypatch.setattr(weekly.friday_reuse, "run", lambda root: (_ for _ in ()).throw(RuntimeError("REUSE_FAILURE")))
    monkeypatch.setattr(
        weekly.tactical,
        "run",
        lambda *args, **kwargs: pytest.fail("tactical must not run after required Friday reuse failure"),
    )

    with pytest.raises(RuntimeError, match="WEEKLY_TAIL_REQUIRED_FRIDAY_REUSE_FAILED"):
        weekly.run(root=tmp_path)

    audit = json.loads((tmp_path / "outputs/audit/WEEKLY_TAIL_SUPER_RUNTIME_V21_16_0.json").read_text())
    assert audit["status"] == "FAILED_REQUIRED_FRIDAY_TACTICAL_REUSE"


def test_weekly_tail_fails_when_required_finalization_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(weekly.etf_replay, "run", lambda root: _ok())
    monkeypatch.setattr(weekly.friday_reuse, "run", lambda root: _ok())

    def tactical_ok(root, *, tct_complete_callback=None):
        assert tct_complete_callback is not None
        tct_complete_callback(_ok(), None)
        return _ok()

    monkeypatch.setattr(weekly.tactical, "run", tactical_ok)
    monkeypatch.setattr(weekly.postmarket, "run", lambda root: _ok())
    monkeypatch.setattr(weekly.decision_brief, "run", lambda root: (_ for _ in ()).throw(RuntimeError("BRIEF_FAILURE")))
    monkeypatch.setattr(weekly.weekly_post, "run", lambda root: _ok())

    with pytest.raises(RuntimeError, match="WEEKLY_TAIL_REQUIRED_FINALIZATION_FAILED:decision_brief"):
        weekly.run(root=tmp_path)

    audit = json.loads((tmp_path / "outputs/audit/WEEKLY_TAIL_SUPER_RUNTIME_V21_16_0.json").read_text())
    assert audit["status"] == "FAILED_REQUIRED_WEEKLY_FINALIZATION"
    assert audit["required_failures"] == ["decision_brief"]
