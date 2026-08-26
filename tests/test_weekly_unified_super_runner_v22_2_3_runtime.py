from __future__ import annotations

import json

from v182.reporting import weekly_unified_super_runner_v22_2_3 as weekly


def test_weekly_defaults_slow_sources_to_governed_cache_preferred(monkeypatch, tmp_path):
    observed: dict[str, str | None] = {}
    monkeypatch.delenv("PEA_SLOW_SOURCE_MODE", raising=False)
    monkeypatch.delenv("V182_RUN_ID", raising=False)

    def previous_run(root):
        observed["mode"] = weekly.os.environ.get("PEA_SLOW_SOURCE_MODE")
        return {"status": "SUCCESS"}

    monkeypatch.setattr(weekly.previous, "run", previous_run)
    monkeypatch.setattr(
        weekly.ci_light_v4,
        "run",
        lambda root: {"status": "SUCCESS", "selected": 0, "selected_by_horizon": {}},
    )

    result = weekly.run(root=tmp_path)

    assert result["status"] == "SUCCESS"
    assert observed["mode"] == "CACHE_PREFERRED"
    assert "PEA_SLOW_SOURCE_MODE" not in weekly.os.environ
    assert "V182_RUN_ID" not in weekly.os.environ
    audit = json.loads(
        (tmp_path / "outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V22_2_3.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["weekly_runtime_target_seconds"] == 1200
    assert audit["missing_and_hard_stale_refresh_preserved"] is True
    assert audit["checkpoint_scope_unique_per_weekly_invocation"] is True
    assert audit["checkpoint_run_id"].startswith("weekly-")


def test_explicit_live_maintenance_mode_is_preserved(monkeypatch, tmp_path):
    observed: dict[str, str | None] = {}
    monkeypatch.setenv("PEA_SLOW_SOURCE_MODE", "LIVE")
    monkeypatch.setenv("V182_RUN_ID", "governed-maintenance-run")
    monkeypatch.setattr(
        weekly.previous,
        "run",
        lambda root: observed.update(mode=weekly.os.environ.get("PEA_SLOW_SOURCE_MODE"))
        or {"status": "SUCCESS"},
    )
    monkeypatch.setattr(
        weekly.ci_light_v4,
        "run",
        lambda root: {"status": "SUCCESS", "selected": 0, "selected_by_horizon": {}},
    )

    weekly.run(root=tmp_path)

    assert observed["mode"] == "LIVE"
    assert weekly.os.environ["PEA_SLOW_SOURCE_MODE"] == "LIVE"
    assert weekly.os.environ["V182_RUN_ID"] == "governed-maintenance-run"
