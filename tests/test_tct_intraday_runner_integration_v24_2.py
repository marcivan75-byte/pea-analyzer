from __future__ import annotations

import json

import numpy as np
import pandas as pd

from v182.reporting import tct_intraday_shadow_run as runner


def _intraday_history() -> pd.DataFrame:
    frames = []
    sessions = pd.bdate_range("2026-07-01", "2026-07-20")[:12]
    for day_number, day in enumerate(sessions):
        idx = pd.date_range(pd.Timestamp(day.date()) + pd.Timedelta(hours=9), periods=30, freq="5min")
        base = 100.0 + 0.10 * day_number
        close = base + np.linspace(0.0, 0.30, 30)
        volume = np.full(30, 1000.0)
        if day_number == 11:
            close[20:] += np.linspace(0.10, 2.0, 10)
            volume[20:] = 2500.0
        frames.append(
            pd.DataFrame(
                {
                    "open": close - 0.02,
                    "high": close + 0.05,
                    "low": close - 0.05,
                    "close": close,
                    "volume": volume,
                },
                index=idx,
            )
        )
    return pd.concat(frames)


def test_shadow_runner_persists_only_sessions_after_daily_signal(tmp_path, monkeypatch):
    cfg = {
        "version": "TCT_V24.2.0_INTRADAY_SCALPING_SHADOW",
        "governance": {"holdout_locked": True},
        "signal_bridge": {
            "eligible_source_decisions": ["T1_STARTER_25_SHADOW", "T1_WATCH_SHADOW", "T2_CONFIRM_75_SHADOW"],
            "minimum_lag_sessions": 1,
            "max_execution_sessions_after_signal": 3,
            "ledger_path": "state/TCT_V24_2_0_SIGNAL_LEDGER.csv",
            "observation_ledger_path": "state/TCT_V24_2_0_INTRADAY_OBSERVATIONS.csv",
        },
        "intraday_data": {
            "interval": "5m",
            "bootstrap_period": "60d",
            "cache_dir": "data/cache/actions_intraday_5m",
            "opening_range_bars": 3,
            "rvol_lookback_sessions": 10,
            "rvol_min_sessions": 5,
            "minimum_session_bars": 18,
            "breakout_lookback_bars": 20,
            "retest_lookback_bars": 6,
        },
        "diagnostic_weights": {
            "rvol_volume_acceleration": 0.20,
            "vwap_timing": 0.15,
            "structure": 0.20,
            "intraday_volatility": 0.15,
            "liquidity_execution": 0.15,
            "momentum_5m": 0.10,
            "order_flow_optional": 0.05,
        },
        "shadow_thresholds": {
            "minimum_weighted_coverage": 0.65,
            "entry_ready_score": 72.0,
            "entry_strong_score": 82.0,
            "rvol_confirmation_min": 1.20,
            "range_expansion_min": 1.10,
            "max_vwap_extension_pct": 0.025,
            "minimum_turnover_ratio": 0.75,
            "max_spread_pct_if_available": 0.006,
        },
        "setups": {
            "EXPLOSIVE_BREAKOUT": {"enabled": True},
            "BREAKOUT_RETEST": {"enabled": True},
            "VWAP_RECLAIM": {"enabled": True},
            "OPENING_RANGE_BREAKOUT": {"enabled": True},
        },
    }
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "TCT_V24_2_0_INTRADAY_SHADOW.json").write_text(json.dumps(cfg), encoding="utf-8")
    (tmp_path / "outputs" / "daily_tct_ct").mkdir(parents=True)
    snapshot = pd.DataFrame(
        [
            {
                "isin": "FR0000000001",
                "name": "Synthetic",
                "decision": "T1_STARTER_25_SHADOW",
                "setup": "T1",
                "t1_source_event_id": "T1_TEST",
                "t1_quality_score": 85.0,
                "t2_quality_score": np.nan,
            }
        ]
    )
    snapshot.to_csv(tmp_path / "outputs" / "daily_tct_ct" / "TCT_SHADOW_V24_1_7.csv", sep=";", index=False)
    actions = pd.DataFrame([{"isin": "FR0000000001", "name": "Synthetic", "yahoo_ticker": "SYN.PA"}])
    actions.to_csv(tmp_path / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", sep=";", index=False)

    history = _intraday_history()
    signal_date = str(sorted(set(pd.Index(history.index.date).astype(str)))[-2])
    monkeypatch.setattr(runner, "_latest_daily_dates", lambda _cache, _tickers: {"SYN.PA": signal_date})
    monkeypatch.setattr(runner, "download_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_extract_histories", lambda _cache, _wanted: {"SYN.PA": history})

    payload = runner.run(tmp_path)
    observations = pd.read_csv(tmp_path / "state" / "TCT_V24_2_0_INTRADAY_OBSERVATIONS.csv", sep=";", low_memory=False)

    assert payload["status"] == "SUCCESS_SHADOW"
    assert payload["new_signals"] == 1
    assert not observations.empty
    assert (observations["source_signal_date"].astype(str) == signal_date).all()
    assert (observations["session_date"].astype(str) > signal_date).all()
    assert not (observations["session_date"].astype(str) == signal_date).any()
    assert (pd.to_numeric(observations["decision_influence"]) == 0.0).all()
    assert (pd.to_numeric(observations["score_influence"]) == 0.0).all()
    assert (pd.to_numeric(observations["sizing_execution_influence"]) == 0.0).all()
    assert (pd.to_numeric(observations["stop_loss_influence"]) == 0.0).all()
    assert not observations["real_orders_enabled"].astype(bool).any()
