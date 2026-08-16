import pandas as pd

from v182.backtest.action_management_v21_8 import simulate_profit_protection


def _path(closes):
    idx = pd.date_range("2024-01-02", periods=len(closes), freq="B")
    return pd.DataFrame({"close": closes}, index=idx)


def test_profit_protection_does_not_cap_winner_without_giveback():
    p = _path([100, 106, 112, 118, 125])
    out = simulate_profit_protection(p, activation=0.05, giveback=0.33)
    assert out["triggered"] is False
    assert out["exit_return"] == out["final_return"]


def test_profit_protection_triggers_only_after_activation_and_giveback():
    p = _path([100, 106, 110, 108, 106])
    out = simulate_profit_protection(p, activation=0.05, giveback=0.33)
    assert out["triggered"] is True
    assert out["exit_return"] > 0


def test_profit_protection_never_behaves_as_fixed_take_profit():
    p = _path([100, 104, 108, 112, 116, 120])
    out = simulate_profit_protection(p, activation=0.05, giveback=0.25)
    assert out["triggered"] is False
    assert out["max_mfe"] >= 0.19
