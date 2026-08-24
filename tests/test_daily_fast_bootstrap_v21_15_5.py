from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from v182.reporting import daily_consolidated_runner_v21_15_4 as consolidated
from v182.reporting import daily_fast_collection_run as collection


def test_disabled_fast_state_still_captures_enriched_masters(monkeypatch, tmp_path: Path) -> None:
    saved: list[tuple[str, int]] = []

    def fake_save_master(frame: pd.DataFrame, path) -> None:
        saved.append((Path(path).name, len(frame)))

    monkeypatch.setattr(collection.legacy, "save_master", fake_save_master)

    runtime = collection.DailyFastRuntime(
        pd.DataFrame(),
        pd.DataFrame(),
        {},
        "DISABLED",
        datetime.now(timezone.utc),
    )
    assert runtime.enabled is False

    actions = pd.DataFrame({"isin": ["FR0000000001"], "score": [1.0]})
    etf = pd.DataFrame({"isin": ["FR0000000002"], "score": [2.0]})

    consolidated._bootstrap_safe_fast_install(runtime)
    try:
        collection.legacy.save_master(actions, tmp_path / collection._ACTION_OUTPUT)
        collection.legacy.save_master(etf, tmp_path / collection._ETF_OUTPUT)
    finally:
        consolidated._bootstrap_safe_fast_restore(runtime)

    assert saved == [
        (collection._ACTION_OUTPUT, 1),
        (collection._ETF_OUTPUT, 1),
    ]
    pd.testing.assert_frame_equal(runtime.captured["ACTION"], actions)
    pd.testing.assert_frame_equal(runtime.captured["ETF"], etf)
    assert collection.legacy.save_master is fake_save_master
