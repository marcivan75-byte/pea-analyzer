from v182.reporting import weekly_operational_runner_v4_3 as runner


def test_operational_runner_reuses_completed_core_outputs(monkeypatch, tmp_path):
    light = {"status": "SUCCESS", "selected": 2}
    monkeypatch.setattr(runner.core, "run", lambda root: {"status": "SUCCESS", "ci_light_v4_2_independent": light})
    monkeypatch.setattr(runner.tail, "run", lambda root: {"status": "SUCCESS_WEEKLY_TAIL_OPTIMIZED"})
    overlay_calls = []

    def overlay_run(**kwargs):
        overlay_calls.append(kwargs)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(runner.overlay, "run", overlay_run)
    monkeypatch.setattr(runner.objectives_risk, "run", lambda root: {"status": "SUCCESS"})
    monkeypatch.setattr(runner.objectives_risk_challenger, "run", lambda root: {"status": "SUCCESS"})
    monkeypatch.setattr(runner.sector_or_shadow, "run", lambda root: {"status": "SUCCESS"})
    monkeypatch.setattr(runner.portfolio_budget, "run", lambda root: {"status": "SUCCESS"})
    monkeypatch.setattr(runner.challenger_publication, "run", lambda root: {"status": "SUCCESS"})

    payload = runner.run(tmp_path)

    assert payload["under_target"] is True
    assert overlay_calls == [{
        "root": tmp_path,
        "ensure_upstream": False,
        "run_ci_light": False,
        "existing_ci_light": light,
    }]
    assert payload["runtime_optimizations"]["information_loss"] is False
    assert payload["runtime_optimizations"]["duplicate_ci_light_run_removed"] is True

