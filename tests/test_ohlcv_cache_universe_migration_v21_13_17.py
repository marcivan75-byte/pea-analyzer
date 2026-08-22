from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd

from v182.sources.yfinance_bulk import (
    CACHE_FORMAT_VERSION,
    DEFAULT_BOOTSTRAP_START,
    DEFAULT_NEGATIVE_CACHE_TTL_DAYS,
    DEFAULT_ROLLING_MONTHS,
    _cache_is_usable,
    _migrate_cache_ticker_universe,
    _negative_cache_active,
)


def _frame(tickers: list[str]) -> pd.DataFrame:
    columns = pd.MultiIndex.from_product([tickers, ["Open", "Close"]])
    data = []
    for row in range(3):
        values = []
        for idx, _ticker in enumerate(tickers):
            values.extend([10.0 + idx + row, 10.5 + idx + row])
        data.append(values)
    return pd.DataFrame(data, index=pd.date_range("2026-08-18", periods=3), columns=columns)


def _manifest(tickers: list[str], *, version: int = 4) -> dict:
    return {
        "cache_format_version": version,
        "updated_at_utc": "2026-08-22T08:00:00+00:00",
        "requested": len(tickers),
        "requested_tickers": tickers,
        "cached_tickers": tickers,
        "successful": tickers,
        "failed": [],
        "actions_requested": True,
        "interval": "1d",
        "batch_size": 2,
        "auto_adjust": True,
        "bootstrap_start": DEFAULT_BOOTSTRAP_START,
        "rolling_months": DEFAULT_ROLLING_MONTHS,
    }


def test_compatible_universe_change_is_resharded_locally_without_history_loss(tmp_path: Path):
    cache = tmp_path / "actions"
    cache.mkdir()
    old_tickers = ["AAA.PA", "BBB.PA", "CCC.PA"]
    _frame(old_tickers[:2]).to_parquet(cache / "history_00000.parquet")
    _frame(old_tickers[2:]).to_parquet(cache / "history_00002.parquet")
    (cache / "history_manifest.json").write_text(
        json.dumps(_manifest(old_tickers)), encoding="utf-8"
    )

    new_tickers = ["AAA.PA", "CCC.PA", "NEW.PA"]
    assert _migrate_cache_ticker_universe(
        cache,
        new_tickers,
        "1d",
        2,
        True,
        True,
        DEFAULT_BOOTSTRAP_START,
        DEFAULT_ROLLING_MONTHS,
    ) is True

    assert _cache_is_usable(
        cache,
        new_tickers,
        "1d",
        2,
        True,
        True,
        DEFAULT_BOOTSTRAP_START,
        DEFAULT_ROLLING_MONTHS,
    ) is True
    manifest = json.loads((cache / "history_manifest.json").read_text(encoding="utf-8"))
    assert manifest["cache_format_version"] == CACHE_FORMAT_VERSION
    assert manifest["universe_migration"]["network_calls"] == 0
    assert manifest["universe_migration"]["added"] == ["NEW.PA"]
    assert manifest["universe_migration"]["removed"] == ["BBB.PA"]
    assert set(manifest["cached_tickers"]) == {"AAA.PA", "CCC.PA"}

    first = pd.read_parquet(cache / "history_00000.parquet")
    second = pd.read_parquet(cache / "history_00002.parquet")
    assert "AAA.PA" in first.columns.get_level_values(0)
    assert "CCC.PA" in first.columns.get_level_values(0)
    assert "BBB.PA" not in first.columns.get_level_values(0)
    assert second.empty


def test_incompatible_semantics_do_not_migrate(tmp_path: Path):
    cache = tmp_path / "actions"
    cache.mkdir()
    old_tickers = ["AAA.PA"]
    _frame(old_tickers).to_parquet(cache / "history_00000.parquet")
    manifest = _manifest(old_tickers)
    manifest["auto_adjust"] = False
    (cache / "history_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert _migrate_cache_ticker_universe(
        cache,
        ["AAA.PA", "NEW.PA"],
        "1d",
        2,
        True,
        True,
        DEFAULT_BOOTSTRAP_START,
        DEFAULT_ROLLING_MONTHS,
    ) is False
    assert (cache / "history_00000.parquet").exists()


def test_negative_cache_has_bounded_ttl_and_expires():
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    recent = {
        "last_failed_at_utc": (now - timedelta(days=2)).isoformat(),
        "reason": "EMPTY_OR_NO_PRICE_DATA",
    }
    expired = {
        "last_failed_at_utc": (now - timedelta(days=8)).isoformat(),
        "reason": "EMPTY_OR_NO_PRICE_DATA",
    }
    assert _negative_cache_active(recent, DEFAULT_NEGATIVE_CACHE_TTL_DAYS, now=now) is True
    assert _negative_cache_active(expired, DEFAULT_NEGATIVE_CACHE_TTL_DAYS, now=now) is False
    assert _negative_cache_active(recent, 0, now=now) is False
