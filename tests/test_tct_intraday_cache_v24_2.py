from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from v182.reporting import tct_intraday_shadow_run as runner


def _history() -> pd.DataFrame:
    idx = pd.date_range("2026-08-18 09:00", periods=30, freq="5min")
    return pd.DataFrame(
        {
            "open": 100.0,
            "high": 100.2,
            "low": 99.8,
            "close": 100.1,
            "volume": 1000.0,
        },
        index=idx,
    )


def test_ticker_cache_dir_is_stable_and_isolated(tmp_path):
    root = tmp_path / "intraday"
    a1 = runner._ticker_cache_dir(root, "AIR.PA")
    a2 = runner._ticker_cache_dir(root, "air.pa")
    b = runner._ticker_cache_dir(root, "MC.PA")
    assert a1 == a2
    assert a1 != b
    assert a1.parent == root
    assert b.parent == root


def test_dynamic_candidate_sets_do_not_share_one_yfinance_cache_identity(tmp_path, monkeypatch):
    calls = []
    hist = _history()

    def fake_download(tickers, cache_dir, **kwargs):
        calls.append((tuple(tickers), cache_dir, kwargs))
        return SimpleNamespace(successful=list(tickers), failed=[])

    def fake_extract(cache_dir, wanted):
        ticker = next(iter(wanted))
        return {ticker: hist.copy()}

    monkeypatch.setattr(runner, "download_history", fake_download)
    monkeypatch.setattr(runner, "_extract_histories", fake_extract)

    icfg = {
        "bootstrap_period": "60d",
        "interval": "5m",
    }
    cache_root = tmp_path / "actions_intraday_5m"

    histories1, failures1 = runner._download_intraday_histories(["AIR.PA"], cache_root, icfg)
    histories2, failures2 = runner._download_intraday_histories(["AIR.PA", "MC.PA"], cache_root, icfg)

    assert failures1 == []
    assert failures2 == []
    assert set(histories1) == {"AIR.PA"}
    assert set(histories2) == {"AIR.PA", "MC.PA"}

    air_calls = [c for c in calls if c[0] == ("AIR.PA",)]
    mc_calls = [c for c in calls if c[0] == ("MC.PA",)]
    assert len(air_calls) == 2
    assert len(mc_calls) == 1
    assert air_calls[0][1] == air_calls[1][1]
    assert air_calls[0][1] != mc_calls[0][1]
    assert all(call[2]["batch_size"] == 1 for call in calls)


def test_runner_declares_per_ticker_cache_layout():
    assert runner.CACHE_LAYOUT == "PER_TICKER_V1"
