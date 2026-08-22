from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.reporting.action_ct_shadow_bundle_run import SharedHistoryLoader


ROOT = Path(__file__).resolve().parents[1]


def test_shared_loader_reuses_physical_load_and_isolates_consumers(tmp_path):
    calls: list[set[str]] = []
    source = {
        "AAA.PA": pd.DataFrame({"close": [10.0, 11.0]}),
        "BBB.PA": pd.DataFrame({"close": [20.0, 21.0]}),
    }

    def governed_loader(_cache_dir: Path, wanted: set[str]) -> dict[str, pd.DataFrame]:
        calls.append(set(wanted))
        return {ticker: source[ticker].copy(deep=True) for ticker in wanted if ticker in source}

    loader = SharedHistoryLoader(governed_loader)
    first = loader(tmp_path, {"AAA.PA", "BBB.PA"})
    first["AAA.PA"].iloc[0, 0] = 999.0
    second = loader(tmp_path, {"AAA.PA", "BBB.PA"})

    assert len(calls) == 1
    assert float(second["AAA.PA"].iloc[0, 0]) == 10.0
    audit = loader.audit()
    assert audit["logical_history_requests"] == 2
    assert audit["physical_parquet_batch_loads"] == 1
    assert audit["avoided_physical_batch_loads"] == 1
    assert audit["consumer_isolation"] == "DEEP_COPY_PER_MODEL"


def test_shared_loader_expands_fail_safe_instead_of_returning_missing_tickers(tmp_path):
    calls: list[set[str]] = []

    def governed_loader(_cache_dir: Path, wanted: set[str]) -> dict[str, pd.DataFrame]:
        calls.append(set(wanted))
        return {ticker: pd.DataFrame({"close": [1.0]}) for ticker in wanted}

    loader = SharedHistoryLoader(governed_loader)
    loader(tmp_path, {"AAA.PA"})
    expanded = loader(tmp_path, {"AAA.PA", "BBB.PA"})

    assert len(calls) == 2
    assert calls[-1] == {"AAA.PA", "BBB.PA"}
    assert set(expanded) == {"AAA.PA", "BBB.PA"}


def test_v220_v221_share_same_governed_history_contract():
    cfg0 = json.loads((ROOT / "config" / "ACTION_CT_V22_0_0_SHADOW.json").read_text(encoding="utf-8"))
    cfg1 = json.loads((ROOT / "config" / "ACTION_CT_V22_1_0_SHADOW.json").read_text(encoding="utf-8"))

    assert cfg0["data_policy"]["source_cache"] == cfg1["data_policy"]["source_cache"] == "data/cache/actions"
    for field in (
        "daily_ohlcv_only",
        "weekly_derived_from_daily",
        "completed_daily_bars_only",
        "defer_current_day_before_local_close",
        "local_close_guard_timezone",
        "local_close_guard_hour",
        "intraday_forbidden",
        "five_minute_forbidden",
        "new_market_data_downloads_required",
    ):
        assert cfg0["data_policy"][field] == cfg1["data_policy"][field]


def test_daily_and_weekly_workflows_keep_v211310_bundle_inside_tactical_bundle():
    daily = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    weekly = (ROOT / ".github" / "workflows" / "committee_master_daily.yml").read_text(encoding="utf-8")
    tactical = (ROOT / "src" / "v182" / "reporting" / "tactical_shadow_bundle_run.py").read_text(encoding="utf-8")

    for workflow in (daily, weekly):
        assert workflow.count("python -m v182.reporting.tactical_shadow_bundle_run") == 1
        assert "python -m v182.reporting.action_ct_shadow_run_v22_0" not in workflow
        assert "python -m v182.reporting.action_ct_shadow_run_v22_1" not in workflow
        assert "tct_next_session_catalyst_run_v24_4_2" in workflow

    assert "action_ct_bundle.run(root=root)" in tactical
    assert "ACTION_CT_SHARED_HISTORY_RUNTIME_V21_13_10.json" in daily


def test_bundle_source_preserves_model_order_and_separate_failure_containment():
    source = (ROOT / "src" / "v182" / "reporting" / "action_ct_shadow_bundle_run.py").read_text(encoding="utf-8")

    assert '_run_model("ACTION_CT_V22.0", v220.run, root, now)' in source
    assert '_run_model("ACTION_CT_V22.1", v221.run, root, now)' in source
    assert source.index('"ACTION_CT_V22.0", v220.run') < source.index('"ACTION_CT_V22.1", v221.run')
    assert "v220._extract_histories = shared_loader" in source
    assert "v220._extract_histories = original_loader" in source
    assert '"decision_logic_changed": False' in source
    assert '"criteria_changed": False' in source
    assert '"weights_changed": False' in source
    assert '"thresholds_changed": False' in source
