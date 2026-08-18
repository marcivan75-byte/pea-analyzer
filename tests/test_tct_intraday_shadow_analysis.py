from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.reporting.tct_intraday_shadow_analysis import build_metrics, run


ROOT = Path(__file__).resolve().parents[1]


def _observations() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "signal_key": "a",
                "session_date": "2026-08-19",
                "source_signal_date": "2026-08-18",
                "status": "CAUSAL_ENTRY_EVENT",
                "isin": "FR1",
                "setup": "EXPLOSIVE_BREAKOUT",
                "source_decision": "T1_STARTER_25_SHADOW",
                "signal_time": "2026-08-19T10:05:00+02:00",
                "score": 85.0,
                "shadow_state": "ENTRY_STRONG_SHADOW",
                "close_return_pct": 0.01,
                "mfe_to_close_pct": 0.02,
                "mae_to_close_pct": -0.003,
            },
            {
                "signal_key": "b",
                "session_date": "2026-08-19",
                "source_signal_date": "2026-08-18",
                "status": "CAUSAL_ENTRY_EVENT",
                "isin": "FR2",
                "setup": "VWAP_RECLAIM",
                "source_decision": "T2_CONFIRM_75_SHADOW",
                "signal_time": "2026-08-19T14:10:00+02:00",
                "score": 76.0,
                "shadow_state": "ENTRY_READY_SHADOW",
                "close_return_pct": -0.005,
                "mfe_to_close_pct": 0.004,
                "mae_to_close_pct": -0.008,
            },
            {
                "signal_key": "c",
                "session_date": "2026-08-19",
                "source_signal_date": "2026-08-18",
                "status": "NO_ENTRY_EVENT",
                "isin": "FR3",
                "setup": np.nan,
                "source_decision": "T1_WATCH_SHADOW",
                "signal_time": "2026-08-19T16:30:00+02:00",
                "score": 65.0,
                "shadow_state": "WAIT_SHADOW",
                "close_return_pct": np.nan,
                "mfe_to_close_pct": np.nan,
                "mae_to_close_pct": np.nan,
            },
        ]
    )


def test_shadow_analytics_computes_descriptive_metrics_without_retuning():
    enriched, metrics = build_metrics(_observations())
    all_row = metrics[(metrics["group_type"] == "ALL") & (metrics["group_value"] == "ALL")].iloc[0]
    assert int(all_row["entry_events"]) == 2
    assert float(all_row["gross_expectancy_close_pct"]) == 0.25
    assert float(all_row["win_rate_pct"]) == 50.0
    assert float(all_row["profit_factor_gross"]) == 2.0
    assert bool(all_row["promotion_authority"]) is False
    assert enriched.set_index("signal_key").loc["a", "session_lag"] == 1
    assert enriched.set_index("signal_key").loc["a", "entry_clock_bucket"] == "10_12"
    assert enriched.set_index("signal_key").loc["a", "score_bucket"] == "GE82"


def test_shadow_analytics_run_stays_early_and_has_zero_authority(tmp_path):
    source_cfg = json.loads((ROOT / "config" / "TCT_V24_2_0_INTRADAY_SHADOW.json").read_text(encoding="utf-8"))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "TCT_V24_2_0_INTRADAY_SHADOW.json").write_text(json.dumps(source_cfg), encoding="utf-8")
    observation_path = tmp_path / source_cfg["signal_bridge"]["observation_ledger_path"]
    observation_path.parent.mkdir(parents=True)
    _observations().to_csv(observation_path, sep=";", index=False, encoding="utf-8-sig")

    payload = run(tmp_path)
    assert payload["status"] == "SUCCESS_SHADOW_ANALYTICS"
    assert payload["maturity"]["status"] == "ACCUMULATING_EARLY"
    assert payload["maturity"]["entry_events"] == 2
    assert payload["promotion_authority"] is False
    assert payload["retuning_allowed"] is False
    assert payload["decision_influence"] == 0.0
    assert payload["score_influence"] == 0.0
    assert payload["sizing_execution_influence"] == 0.0
    assert payload["stop_loss_influence"] == 0.0
    assert payload["holdout_opened"] is False
    assert payload["real_orders_enabled"] is False
    assert payload["net_expectancy_computed"] is False


def test_shadow_analytics_config_forbids_premature_review():
    cfg = json.loads((ROOT / "config" / "TCT_V24_2_0_INTRADAY_SHADOW.json").read_text(encoding="utf-8"))
    analysis = cfg["analysis"]
    assert analysis["version"] == "TCT_V24.2.1_SHADOW_ANALYTICS"
    assert analysis["minimum_entry_events_for_descriptive_metrics"] >= 10
    assert analysis["minimum_entry_events_for_candidate_review"] >= 30
    assert analysis["minimum_entry_events_per_setup_for_review"] >= 15
    assert analysis["minimum_distinct_isins_for_candidate_review"] >= 10
    assert analysis["promotion_authority"] is False
    assert analysis["retuning_allowed"] is False
