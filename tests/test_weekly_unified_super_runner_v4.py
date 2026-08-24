from v182.reporting import weekly_unified_super_runner_v4 as runner


def test_exit_code_has_no_fragile_ancestor_chain():
    assert runner.exit_code({"status": "SUCCESS"}) == 0
    assert runner.exit_code({"status": "FAILED"}) == 2


def test_runner_invariants_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.ci_selection_gate_v4, "run", lambda **kwargs: {"status": "SUCCESS"})
    seen = {}
    def light_run(**kwargs):
        seen.update(kwargs)
        return {"status": "SUCCESS"}
    monkeypatch.setattr(runner.ci_light_v4, "run", light_run)
    payload = runner.run(tmp_path)
    assert payload["status"] == "SUCCESS"
    audit = (tmp_path / runner.AUDIT).read_text(encoding="utf-8")
    assert '"investing_enabled": false' in audit
    assert '"etf_analyst_consensus_required": false' in audit
    assert seen["reuse_selection_context"] is True
    assert '"source_collection_passes": 1' in audit
