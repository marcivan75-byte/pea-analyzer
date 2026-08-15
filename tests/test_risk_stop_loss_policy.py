import pandas as pd

from v182.risk.stop_loss_policy import apply_stop_loss_plan, stop_pct_for


CFG = {
    "action_stop_loss_pct": {"TCT": 6.0, "CT": 8.0, "MT": 12.0, "LT": 18.0},
    "etf_stop_loss_pct": {"MT": 18.0},
    "execution": {"gap_and_slippage_can_exceed_stop": True},
}


def test_asset_specific_stop_percentages_are_preserved():
    assert stop_pct_for("ACTION", "TCT", CFG) == 6.0
    assert stop_pct_for("ACTION", "CT", CFG) == 8.0
    assert stop_pct_for("ACTION", "MT", CFG) == 12.0
    assert stop_pct_for("ACTION", "LT", CFG) == 18.0
    assert stop_pct_for("ETF", "MT", CFG) == 18.0
    assert stop_pct_for("ETF", "CT", CFG) is None


def test_stop_plan_computes_reference_levels_without_changing_scores():
    frame = pd.DataFrame(
        [
            {"asset_class": "ACTION", "horizon": "CT", "isin": "FRA", "score": 81.0, "price": 100.0},
            {"asset_class": "ETF", "horizon": "MT", "isin": "FRE", "score": 90.0, "last_close": 200.0},
        ]
    )
    out = apply_stop_loss_plan(frame, CFG)
    assert out.loc[0, "stop_loss_pct"] == 8.0
    assert out.loc[0, "stop_loss_price"] == 92.0
    assert out.loc[1, "stop_loss_pct"] == 18.0
    assert out.loc[1, "stop_loss_price"] == 164.0
    assert out["score"].tolist() == [81.0, 90.0]
    assert out["stop_loss_gap_slippage_warning"].all()
