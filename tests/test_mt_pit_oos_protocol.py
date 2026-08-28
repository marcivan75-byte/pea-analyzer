from pathlib import Path
import json

from v182.backtest.mt_pit_oos import evaluate, load_protocol


ROOT = Path(__file__).resolve().parents[1]


def test_protocols_are_locked_and_holdout_closed():
    for scope in ("ETF_MT", "ACTION_MT"):
        protocol = load_protocol(ROOT, scope)
        assert protocol["locked_before_results"] is True
        assert protocol["governance"]["final_holdout_locked"] is True
        assert protocol["governance"]["no_score_weight_changes"] is True
        assert protocol["governance"]["no_threshold_retuning"] is True
        assert protocol["governance"]["no_real_orders"] is True
        assert protocol["governance"]["decision_influence"] == 0.0
        assert protocol["outcomes"]["entry_rule"] == "FIRST_TRADING_SESSION_STRICTLY_AFTER_SIGNAL_DATE"
        assert protocol["primary_horizon_days"] == 60


def test_empty_observations_wait_for_history():
    import pandas as pd

    protocol = load_protocol(ROOT, "ETF_MT")
    summary = evaluate(pd.DataFrame(), protocol, "ETF_MT")
    assert summary["status"] == "WAIT_FOR_PIT_HISTORY"
    assert summary["promotion_ready"] is False
    assert summary["decision_influence"] == 0.0


def test_action_mt_forbids_t1_t2():
    protocol = json.loads((ROOT / "config/ACTION_MT_PIT_OOS_PROTOCOL.json").read_text(encoding="utf-8"))
    assert protocol["governance"]["t1_t2_forbidden"] is True
