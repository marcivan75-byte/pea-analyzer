from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.reporting.tactical_shadow_bundle_run import ParquetReadCache


ROOT = Path(__file__).resolve().parents[1]


def test_plain_parquet_reads_are_reused_and_consumers_are_isolated(tmp_path):
    path = tmp_path / "history_batch.parquet"
    calls: list[Path] = []
    source = pd.DataFrame({"close": [10.0, 11.0], "volume": [100.0, 120.0]})

    def reader(value, *args, **kwargs):
        assert not args
        assert not kwargs
        calls.append(Path(value))
        return source.copy(deep=True)

    cache = ParquetReadCache(reader)
    first = cache(path)
    first.loc[0, "close"] = 999.0
    second = cache(path)

    assert len(calls) == 1
    assert float(second.loc[0, "close"]) == 10.0
    audit = cache.audit()
    assert audit["logical_read_parquet_calls"] == 2
    assert audit["physical_read_parquet_calls"] == 1
    assert audit["cache_hits"] == 1
    assert audit["raw_consumer_isolation"] == "DEEP_COPY_PER_READ"


def test_non_plain_parquet_calls_pass_through_without_semantic_guessing(tmp_path):
    path = tmp_path / "history_batch.parquet"
    calls: list[dict] = []

    def reader(_value, *args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return pd.DataFrame({"close": [1.0]})

    cache = ParquetReadCache(reader)
    cache(path, columns=["close"])
    cache(path, columns=["close"])

    assert len(calls) == 2
    audit = cache.audit()
    assert audit["physical_read_parquet_calls"] == 0
    assert audit["cache_hits"] == 0
    assert audit["passthrough_calls"] == 2
    assert audit["non_plain_calls_cached"] is False


def test_action_ct_and_tct_share_only_the_same_raw_cache_contract():
    cfg0 = json.loads((ROOT / "config" / "ACTION_CT_V22_0_0_SHADOW.json").read_text(encoding="utf-8"))
    cfg1 = json.loads((ROOT / "config" / "ACTION_CT_V22_1_0_SHADOW.json").read_text(encoding="utf-8"))
    tct = json.loads((ROOT / "config" / "TCT_V24_3_1_DAILY_TRADER_SHADOW.json").read_text(encoding="utf-8"))

    assert cfg0["data_policy"]["source_cache"] == cfg1["data_policy"]["source_cache"] == tct["data_policy"]["source_cache"] == "data/cache/actions"
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
        assert cfg0["data_policy"][field] == cfg1["data_policy"][field] == tct["data_policy"][field]

    assert cfg0["governance"]["t1_t2_forbidden"] is True
    assert cfg1["governance"]["t1_t2_forbidden"] is True
    assert tct["horizon"] == "TCT"


def test_tactical_bundle_preserves_action_order_and_independent_tct_branch():
    source = (ROOT / "src" / "v182" / "reporting" / "tactical_shadow_bundle_run.py").read_text(encoding="utf-8")

    assert '"action_ct_internal_order_preserved": ["ACTION_CT_V22.0", "ACTION_CT_V22.1"]' in source
    assert "action_ct_bundle.run(root=root)" in source
    assert "tct_trader.run(root=root)" in source
    assert "ThreadPoolExecutor(max_workers=2" in source
    assert '"tct_dependency_on_action_ct_outputs": False' in source
    assert '"tct_completion_released_before_action_ct_join": True' in source
    assert 'setattr(pd, "read_parquet", parquet_cache)' in source
    assert 'setattr(pd, "read_parquet", original_read_parquet)' in source
    assert '"governed_extractors_changed": False' in source
    assert '"t1_t2_scope_changed": False' in source
    assert '"criteria_changed": False' in source
    assert '"weights_changed": False' in source
    assert '"thresholds_changed": False' in source


def test_daily_and_weekly_use_tactical_bundle_via_dag_and_keep_downstream_pit_catalyst():
    daily_workflow = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    daily_entry = (ROOT / "src" / "v182" / "reporting" / "daily_consolidated_runner_v21_15_4.py").read_text(encoding="utf-8")
    daily_impl = (ROOT / "src" / "v182" / "reporting" / "daily_consolidated_runner_v21_15_7.py").read_text(encoding="utf-8")
    daily_tactical = (ROOT / "src" / "v182" / "reporting" / "daily_tactical_super_runner_v21_15_4.py").read_text(encoding="utf-8")
    weekly_workflow = (ROOT / ".github" / "workflows" / "committee_master_daily.yml").read_text(encoding="utf-8")
    weekly_tail = (ROOT / "src" / "v182" / "reporting" / "weekly_tail_super_runner_v21_16_0.py").read_text(encoding="utf-8")
    postmarket = (ROOT / "src" / "v182" / "reporting" / "tct_postmarket_bundle_run.py").read_text(encoding="utf-8")

    assert "python -m v182.reporting.daily_consolidated_runner_v21_15_4" in daily_workflow
    assert "daily_consolidated_runner_v21_15_7 as impl" in daily_entry
    assert "daily_tactical_super_runner_v21_15_6 as tactical" in daily_impl
    assert "tactical_shadow_bundle_run as tactical" in daily_tactical
    assert "tct_postmarket_bundle_run as postmarket" in daily_tactical

    assert "python -m v182.reporting.weekly_tail_super_runner_v21_16_0" in weekly_workflow
    assert "tactical_shadow_bundle_run as tactical" in weekly_tail
    assert "tct_postmarket_bundle_run as postmarket" in weekly_tail

    for workflow in (daily_workflow, weekly_workflow):
        assert "python -m v182.reporting.action_ct_shadow_bundle_run" not in workflow
        assert "python -m v182.reporting.tct_daily_trader_shadow_run_v24_3_1" not in workflow

    assert "ohlc_ledger.run(root=root)" in postmarket
    assert 'catalyst.run(root=root, phase="POSTMARKET")' in postmarket
    assert "lineage.run(root=root)" in postmarket
    assert "validator.run(root=root)" in postmarket
    assert "TACTICAL_SHARED_PARQUET_RUNTIME_V21_13_11.json" in daily_workflow
    assert "TACTICAL_SHARED_PARQUET_RUNTIME_V21_13_11.json" in weekly_workflow
