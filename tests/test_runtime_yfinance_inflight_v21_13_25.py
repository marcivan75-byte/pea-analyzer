from __future__ import annotations

import json
from pathlib import Path
import threading

from v182.sources import yfinance_info


ROOT=Path(__file__).resolve().parents[1]


def test_action_yahoo_inflight_policy_is_conservative_and_etf_is_unchanged() -> None:
    cfg=json.loads((ROOT/"config"/"V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    action=cfg["runtime_optimization"]["yfinance_fundamentals"]
    etf=cfg["runtime_optimization"]["etf_info"]

    assert cfg["yfinance"]["info_delay_seconds"] == 0.4
    assert action["max_workers"] == 6
    assert action["refresh_budget"] == 320
    assert action["ttl_days"] == {"HOT":3,"WARM":10,"COLD":21}
    assert action["hard_max_age_days"] == 35
    assert action["negative_cache_days"] == 7

    # ETF Yahoo remains deliberately untouched in this pass.
    assert etf["max_workers"] == 4
    assert etf["refresh_budget"] == 40
    assert etf["ttl_days"] == {"HOT":7,"WARM":14,"COLD":30}


def test_collect_info_can_use_six_inflight_workers_while_limiter_stays_at_point_four(monkeypatch) -> None:
    barrier=threading.Barrier(6)
    lock=threading.Lock()
    entered=[]
    limiter_intervals=[]

    def fake_collect_one(ticker, yf, limiter):
        del yf
        with lock:
            entered.append(ticker)
            limiter_intervals.append(limiter.min_interval_seconds)
        barrier.wait(timeout=3)
        return [{"ticker":ticker,"field":"market_cap","value":1,"source":"yfinance"}],None

    monkeypatch.setattr(yfinance_info,"_collect_one",fake_collect_one)
    observations,failures=yfinance_info.collect_info(
        [f"TICKER{idx}.PA" for idx in range(6)],
        delay_seconds=0.4,
        max_workers=6,
    )

    assert failures == []
    assert len(entered) == 6
    assert len(observations) == 6
    assert set(limiter_intervals) == {0.4}
