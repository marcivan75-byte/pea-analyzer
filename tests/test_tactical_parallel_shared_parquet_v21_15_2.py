from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

import pandas as pd

from v182.reporting import tactical_shadow_bundle_run as tactical_bundle


def test_thread_safe_parquet_cache_reads_same_plain_path_only_once(tmp_path: Path) -> None:
    physical_reads = 0
    physical_lock = threading.Lock()

    def fake_reader(path):
        nonlocal physical_reads
        with physical_lock:
            physical_reads += 1
        time.sleep(0.03)
        return pd.DataFrame({"value": [1, 2, 3]})

    cache = tactical_bundle.ParquetReadCache(fake_reader)
    path = tmp_path / "same.parquet"
    with ThreadPoolExecutor(max_workers=8) as pool:
        frames = list(pool.map(lambda _idx: cache(path), range(8)))

    assert physical_reads == 1
    assert cache.physical_reads == 1
    assert cache.logical_calls == 8
    assert cache.cache_hits == 7
    assert all(frame.equals(frames[0]) for frame in frames)

    frames[0].loc[0, "value"] = 999
    assert frames[1].loc[0, "value"] == 1
    audit = cache.audit()
    assert audit["thread_safe_cache"] is True
    assert audit["single_physical_read_per_plain_path"] is True
    assert audit["raw_consumer_isolation"] == "DEEP_COPY_PER_READ"


def test_action_ct_and_tct_branches_overlap_without_changing_action_ct_internal_contract(tmp_path, monkeypatch) -> None:
    starts: dict[str, float] = {}
    ends: dict[str, float] = {}
    lock = threading.Lock()

    def action_run(*, root: Path):
        with lock:
            starts["action"] = time.perf_counter()
        time.sleep(0.06)
        with lock:
            ends["action"] = time.perf_counter()
        return {"status": "SUCCESS_ACTION_CT_BUNDLE", "version": "TEST_ACTION_CT"}

    def tct_run(*, root: Path):
        with lock:
            starts["tct"] = time.perf_counter()
        time.sleep(0.06)
        with lock:
            ends["tct"] = time.perf_counter()
        return {"status": "SUCCESS_TCT", "rows": 10}

    monkeypatch.setattr(tactical_bundle.action_ct_bundle, "run", action_run)
    monkeypatch.setattr(tactical_bundle.tct_trader, "run", tct_run)

    payload = tactical_bundle.run(tmp_path)

    assert starts["action"] < ends["tct"]
    assert starts["tct"] < ends["action"]
    assert payload["status"] == "SUCCESS_TACTICAL_PARALLEL_SHARED_RUNTIME"
    assert payload["independent_model_branches_overlapped"] is True
    assert payload["action_ct_internal_order_preserved"] == ["ACTION_CT_V22.0", "ACTION_CT_V22.1"]
    assert payload["tct_dependency_on_action_ct_outputs"] is False
    assert payload["shared_parquet_physical_reads_preserved"] is True
    assert payload["external_provider_concurrency_added"] is False
    assert payload["decision_logic_changed"] is False
    assert payload["weights_changed"] is False
    assert payload["thresholds_changed"] is False
    assert payload["original_pandas_reader_restored"] is True


def test_parallel_bundle_preserves_independent_error_collection_and_restores_pandas(tmp_path, monkeypatch) -> None:
    original_reader = pd.read_parquet

    monkeypatch.setattr(
        tactical_bundle.action_ct_bundle,
        "run",
        lambda *, root: (_ for _ in ()).throw(RuntimeError("action failed")),
    )
    monkeypatch.setattr(
        tactical_bundle.tct_trader,
        "run",
        lambda *, root: {"status": "SUCCESS_TCT", "rows": 3},
    )

    try:
        tactical_bundle.run(tmp_path)
    except RuntimeError as exc:
        assert "ACTION_CT_V22.0_V22.1" in str(exc)
    else:
        raise AssertionError("bundle must propagate step errors after both branches complete")

    assert pd.read_parquet is original_reader


def test_action_ct_v22_1_still_reads_parent_v22_0_output() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "v182" / "reporting" / "action_ct_shadow_run_v22_1.py").read_text(encoding="utf-8")
    assert 'parent = legacy._read_csv(outdir / "ACTION_CT_V22_0_0_SHADOW.csv")' in source


def test_tct_trader_has_no_action_ct_shadow_output_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "v182" / "reporting" / "tct_daily_trader_shadow_run_v24_3_1.py").read_text(encoding="utf-8")
    assert "ACTION_CT_V22_0_0_SHADOW.csv" not in source
    assert "ACTION_CT_V22_1_0_SHADOW.csv" not in source
    assert "state/action_ct" not in source
