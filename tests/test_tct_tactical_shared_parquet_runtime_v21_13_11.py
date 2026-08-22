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


def test_tactical_bundle_preserves_model_order_and_original_extractors():
    source = (ROOT / "src" / "v182" / "reporting" / "tactical_shadow_bundle_run.py").read_text(encoding="utf-8")

    assert '"model_order_preserved": ["ACTION_CT_V22.0", "ACTION_CT_V22.1", "TCT_V24.3.1"]' in source
    assert "action_ct_bundle.run(root=root)" in source
    assert "tct_trader.run(root=root)" in source
    assert source.index("action_ct_bundle.run(root=root)") < source.index("tct_trader.run(root=root)")
    assert "pd.read_parquet = parquet_cache" in source
    assert "pd.read_parquet = original_read_parquet" in source
    assert '"governed_extractors_changed": False' in source
    assert '"t1_t2_scope_changed": False' in source
    assert '"criteria_changed": False' in source
    assert '"weights_changed": False' in source
    assert '"thresholds_changed": False' in source


def test_daily_and_weekly_use_one_tactical_bundle_and_keep_downstream_pit_catalyst():
    daily = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    weekly = (ROOT / ".github" / "workflows" / "committee_master_daily.yml").read_text(encoding="utf-8")

    for workflow in (daily, weekly):
        assert workflow.count("python -m v182.reporting.tactical_shadow_bundle_run") == 1
        assert "python -m v182.reporting.action_ct_shadow_bundle_run" not in workflow
        assert "python -m v182.reporting.tct_daily_trader_shadow_run_v24_3_1" not in workflow
        assert "python -m v182.reporting.tct_pit_ohlc_ledger_v24_4_2" in workflow
        assert "python -m v182.reporting.tct_next_session_catalyst_run_v24_4_2" in workflow
        assert "python -m v182.reporting.tct_v24_4_2_pit_lineage" in workflow
        assert "python -m v182.reporting.tct_v24_4_2_pit_validator" in workflow

    assert "TACTICAL_SHARED_PARQUET_RUNTIME_V21_13_11.json" in daily
    assert "TACTICAL_SHARED_PARQUET_RUNTIME_V21_13_11.json" in weekly
