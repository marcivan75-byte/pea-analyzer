from __future__ import annotations

import pandas as pd

from v182.reporting import daily_consolidated_runner_v21_15_4 as runner


def test_daily_runtime_intercepts_wave09_and_restores_original(monkeypatch):
    calls = {"original_wave09": 0}

    def original_wave09(actions_df, etf_df, cfg, fred_api_key):
        calls["original_wave09"] += 1
        return [{"unexpected": True}], [{"unexpected": True}], {"status": "UNEXPECTED"}

    monkeypatch.setattr(runner.collection.waves, "wave9_topdown", original_wave09)
    original_reference = runner.collection.waves.wave9_topdown

    def fake_collection_run():
        obs_actions, obs_etf, diagnostics = runner.collection.waves.wave9_topdown(
            pd.DataFrame({"isin": ["FR0000000001"]}),
            pd.DataFrame({"isin": ["FR0000000002"]}),
            {},
            "dummy-key",
        )
        return {
            "status": "SUCCESS",
            "wave09_probe": {
                "actions": obs_actions,
                "etf": obs_etf,
                "diagnostics": diagnostics,
            },
        }

    monkeypatch.setattr(runner.collection, "run", fake_collection_run)

    payload, diagnostics = runner._run_collection_optimized_locals()

    assert calls["original_wave09"] == 0
    assert payload["wave09_probe"]["actions"] == []
    assert payload["wave09_probe"]["etf"] == []
    assert payload["wave09_probe"]["diagnostics"]["status"] == "SKIPPED_DAILY_WEEKLY_ONLY"
    assert diagnostics["wave09_daily_policy"]["status"] == "WEEKLY_ONLY"
    assert diagnostics["wave09_daily_policy"]["daily_fred_calls"] == 0
    assert diagnostics["wave09_daily_policy"]["daily_gdelt_calls"] == 0
    assert diagnostics["wave09_daily_policy"]["calls_intercepted"] == 1
    assert diagnostics["wave09_daily_policy"]["weekly_execution_unchanged"] is True
    assert runner.collection.waves.wave9_topdown is original_reference
