from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import numpy as np
import pandas as pd

from v182.reporting.action_mt_shadow_run_v1 import append_pit_idempotent, completed_bars_only, run
from v182.sources.action_mt_cache_v1 import ActionMTHistoryCache


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ACTION_MT_V1_0_0_SHADOW.json").read_text(encoding="utf-8"))


def _history(end: str = "2026-08-21", periods: int = 320) -> pd.DataFrame:
    index = pd.bdate_range(end=end, periods=periods)
    close = np.linspace(80.0, 150.0, periods) + np.sin(np.linspace(0, 20, periods))
    return pd.DataFrame({"open": close * 0.995, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 5_000_000 / close}, index=index)


def _context(isin: str, sector: str) -> dict:
    return {
        "isin": isin, "asset_class": "ACTION", "data_status": "OK", "sector": sector,
        "quality_score": 82, "profitability_score": 80, "balance_sheet_score": 78,
        "earnings_growth_score": 76, "revenue_growth_score": 74, "free_cash_flow_growth_score": 72,
        "valuation_discount_score": 65, "analyst_revisions_score": 75, "target_upside_growth_score": 70,
        "sector_rotation_score": 70, "sector_macro_score": 65, "macro_evidence_sufficient": True,
        "market_regime_score": 70,
    }


def test_cache_reports_hash_hit_and_miss(tmp_path: Path):
    _history().to_csv(tmp_path / "FR0000000001.csv")
    cache = ActionMTHistoryCache(tmp_path, max_staleness_days=7)
    frame, metadata = cache.load("FR0000000001", as_of=pd.Timestamp("2026-08-21"))
    assert not frame.empty and metadata["status"] == "CACHE_HIT"
    assert len(metadata["sha256"]) == 64
    _, missing = cache.load("FR0000000999", as_of=pd.Timestamp("2026-08-21"))
    assert missing["status"] == "CACHE_MISS"
    assert cache.manifest()["hit_rate"] == 0.5


def test_local_close_guard_defers_current_day():
    frame = _history(periods=20)
    before = datetime(2026, 8, 21, 17, 0, tzinfo=ZoneInfo("Europe/Paris"))
    after = datetime(2026, 8, 21, 19, 0, tzinfo=ZoneInfo("Europe/Paris"))
    assert pd.Timestamp(completed_bars_only(frame, before, _cfg()).index.max()).date().isoformat() < "2026-08-21"
    assert pd.Timestamp(completed_bars_only(frame, after, _cfg()).index.max()).date().isoformat() == "2026-08-21"


def test_pit_ledger_is_idempotent(tmp_path: Path):
    ledger = tmp_path / "ledger.csv"
    rows = pd.DataFrame([{"isin": "FR-A", "snapshot_fingerprint": "abc", "score": 80}])
    assert append_pit_idempotent(ledger, rows) == 0
    assert append_pit_idempotent(ledger, rows) == 1
    assert len(pd.read_csv(ledger)) == 1


def test_complete_runner_publishes_governed_package_outputs(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "outputs"
    cache_dir.mkdir()
    master = pd.DataFrame([_context("FR0000000001", "TECH"), _context("FR0000000002", "INDUSTRIALS")])
    for isin in master["isin"]:
        _history().to_csv(cache_dir / f"{isin}.csv")
    report = run(master, _cfg(), ActionMTHistoryCache(cache_dir, 7), output_dir, datetime(2026, 8, 21, 19, tzinfo=ZoneInfo("Europe/Paris")))
    assert report["status"] == "SUCCESS_SHADOW"
    assert report["cache"]["hits"] == 2
    latest = pd.read_csv(output_dir / "ACTION_MT_LATEST.csv")
    for field in ("rr_indicative", "invalidation_atr", "optimal_entry_shadow", "or_reliability_shadow"):
        assert field in latest
    assert latest["or_score_influence"].eq(0.0).all()
    assert latest["or_decision_influence"].eq(0.0).all()
    assert latest["or_real_order_allowed"].astype(str).str.lower().eq("false").all()
    for name in (
        "ACTION_MT_LATEST.csv", "ACTION_MT_PIT_LEDGER.csv", "ACTION_MT_EXCLUSIONS.csv",
        "ACTION_MT_CACHE_MANIFEST.json", "ACTION_MT_RUN_REPORT.json", "ACTION_MT_COMMITTEE.txt",
    ):
        assert (output_dir / name).exists()

