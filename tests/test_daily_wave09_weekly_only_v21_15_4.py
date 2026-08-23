from __future__ import annotations

import pandas as pd

from v182.reporting import daily_consolidated_runner_v21_15_4 as runner


def test_daily_runtime_intercepts_wave09_and_uses_validated_seed_on_bootstrap(monkeypatch):
    calls = {"original_wave09": 0}

    def original_wave09(actions_df, etf_df, cfg, fred_api_key):
        calls["original_wave09"] += 1
        return [{"unexpected": True}], [{"unexpected": True}], {"status": "UNEXPECTED"}

    monkeypatch.setattr(runner.collection.waves, "wave9_topdown", original_wave09)
    original_reference = runner.collection.waves.wave9_topdown
    monkeypatch.setattr(
        runner.base,
        "_load_fast_state_compatible",
        lambda: (pd.DataFrame(), pd.DataFrame(), {}, "DISABLED"),
    )

    def fake_collection_run():
        obs_actions, obs_etf, diagnostics = runner.collection.waves.wave9_topdown(
            pd.DataFrame(
                {
                    "isin": ["FR0010208488"],
                    "country_yf": ["France"],
                    "sector_yf": ["Energy"],
                }
            ),
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
    assert payload["wave09_probe"]["actions"]
    assert payload["wave09_probe"]["etf"] == []
    assert payload["wave09_probe"]["diagnostics"]["status"] == "REUSED_VALIDATED_DAILY_W09_SEED"
    assert payload["wave09_probe"]["diagnostics"]["network_calls"] == 0
    assert diagnostics["wave09_daily_policy"]["status"] == "WEEKLY_ONLY"
    assert diagnostics["wave09_daily_policy"]["daily_fred_calls"] == 0
    assert diagnostics["wave09_daily_policy"]["daily_gdelt_calls"] == 0
    assert diagnostics["wave09_daily_policy"]["calls_intercepted"] == 1
    assert diagnostics["wave09_daily_policy"]["daily_observations_applied"] > 0
    assert diagnostics["wave09_daily_policy"]["weekly_execution_unchanged"] is True
    assert runner.collection.waves.wave9_topdown is original_reference


def test_daily_runtime_retains_existing_w09_without_new_observations(monkeypatch):
    calls = {"original_wave09": 0}
    retained_actions = pd.DataFrame(
        {
            "isin": ["FR0010208488"],
            "funnel_global_macro_score": [64.4034],
            "funnel_market_sentiment_score": [52.8504],
        }
    )
    retained_etf = pd.DataFrame({"isin": ["FR0000000002"]})

    def original_wave09(actions_df, etf_df, cfg, fred_api_key):
        calls["original_wave09"] += 1
        return [{"unexpected": True}], [{"unexpected": True}], {"status": "UNEXPECTED"}

    monkeypatch.setattr(runner.collection.waves, "wave9_topdown", original_wave09)
    original_reference = runner.collection.waves.wave9_topdown
    monkeypatch.setattr(
        runner.base,
        "_load_fast_state_compatible",
        lambda: (retained_actions, retained_etf, {"source": "DAILY_FAST_STATE"}, "DELTA_ONLY"),
    )
    monkeypatch.setattr(runner.base, "refresh_earnings_clock", lambda frame: (frame, {"network_calls": 0}))

    def fake_collection_run():
        obs_actions, obs_etf, diagnostics = runner.collection.waves.wave9_topdown(
            retained_actions,
            retained_etf,
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
    assert payload["wave09_probe"]["diagnostics"]["status"] == "SKIPPED_DAILY_WEEKLY_ONLY_RETAINED_MASTER"
    assert payload["wave09_probe"]["diagnostics"]["fred_calls"] == 0
    assert payload["wave09_probe"]["diagnostics"]["gdelt_calls"] == 0
    assert diagnostics["wave09_daily_policy"]["daily_observations_applied"] == 0
    assert diagnostics["wave09_daily_policy"]["calls_intercepted"] == 1
    assert diagnostics["fast_state_identity"]["resolved_mode"] == "DELTA_ONLY"
    assert runner.collection.waves.wave9_topdown is original_reference
