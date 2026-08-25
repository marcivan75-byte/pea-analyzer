from __future__ import annotations

from pathlib import Path
from threading import Event

from v182.reporting import weekly_unified_super_runner_v22_2_1 as runner


def test_market_orientation_starts_while_weekly_core_runs(monkeypatch, tmp_path: Path):
    market_started = Event()
    core_started = Event()

    def fake_market(*, root):
        market_started.set()
        assert core_started.wait(timeout=2.0)
        return {
            "status": "SUCCESS",
            "total_seconds": 0.01,
            "orientation": {"us": "NEUTRAL", "europe": "NEUTRAL", "global": "NEUTRAL"},
        }

    def fake_core(*, root):
        core_started.set()
        assert market_started.wait(timeout=2.0)
        return {"status": "SUCCESS"}

    def fake_watch(*, root):
        return {
            "status": "SUCCESS",
            "candidate_rows": 0,
            "ready_for_review": 0,
            "wait": 0,
            "market_blocks": 0,
            "market_cautions": 0,
            "potential_available": 0,
        }

    monkeypatch.setattr(runner.market_orientation_v22_2, "run", fake_market)
    monkeypatch.setattr(runner.core, "run", fake_core)
    monkeypatch.setattr(runner.ci_entry_watch_v22_2_1, "run", fake_watch)

    result = runner.run(tmp_path)
    assert result["status"] == "SUCCESS"
    assert result["market_orientation_v22_2"]["orientation"]["global"] == "NEUTRAL"

    audit = (tmp_path / "outputs" / "audit" / runner.AUDIT_NAME).read_text(encoding="utf-8")
    assert '"market_orientation_overlaps_weekly_core": true' in audit
    assert '"market_orientation_overlap_started": true' in audit
