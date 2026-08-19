from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pandas as pd

from v182.sources.global_market_snapshot import fetch_global_market_snapshot


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_4_0_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))


def _fake_download_frame(cfg: dict) -> pd.DataFrame:
    completed = cfg["global_market"]["completed_session_symbols"]
    one_shot = cfg["global_market"]["one_shot_context_symbols"]
    symbols = list(dict.fromkeys([*completed.values(), *one_shot.values()]))
    cols = pd.MultiIndex.from_tuples([("Close", symbol) for symbol in symbols])
    rows = []
    first = []
    second = []
    for symbol in symbols:
        first.append(100.0)
        if symbol in {one_shot["SP500_FUTURE"], one_shot["NASDAQ_FUTURE"]}:
            second.append(103.0)
        elif symbol == completed["VIX"]:
            second.append(99.0)
        else:
            second.append(101.0)
    rows.append(first)
    rows.append(second)
    return pd.DataFrame(rows, index=pd.to_datetime(["2026-08-18", "2026-08-19"]), columns=cols)


def test_global_snapshot_is_one_daily_download_and_preopen_uses_futures_overlay(monkeypatch):
    cfg = _cfg()
    calls = []
    raw = _fake_download_frame(cfg)

    def fake_download(**kwargs):
        calls.append(kwargs)
        return raw

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=fake_download))
    post = fetch_global_market_snapshot(cfg, phase="POSTMARKET")
    pre = fetch_global_market_snapshot(cfg, phase="PREOPEN")

    assert len(calls) == 2  # one call per explicit snapshot, never polling
    assert all(call["interval"] == "1d" for call in calls)
    assert all(call["period"] == "5d" for call in calls)
    assert "SP500_FUTURE" in pre.one_shot_returns_pct
    assert "NASDAQ_FUTURE" in pre.one_shot_returns_pct
    assert pre.risk_on_score is not None and post.risk_on_score is not None
    assert pre.risk_on_score > post.risk_on_score
    assert pre.shock_magnitude_score is not None
    assert pre.source == "YFINANCE_DAILY_SNAPSHOT_ONCE"


def test_v244_global_core_excludes_unfinished_asian_markets():
    cfg = _cfg()
    completed = cfg["global_market"]["completed_session_symbols"]
    core_weights = cfg["global_market"]["risk_on_weights"]
    assert "NIKKEI" in completed
    assert "HANGSENG" not in completed
    assert "SHANGHAI" not in completed
    assert "HANGSENG" not in core_weights
    assert "SHANGHAI" not in core_weights
    assert cfg["global_market"]["exclude_partial_asia_from_core_score"] is True
