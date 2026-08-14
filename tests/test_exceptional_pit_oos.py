from pathlib import Path
import pandas as pd
import pytest

from v182.backtest.exceptional_pit_oos import _action_monthly_frame, run


def test_exceptional_backtest_requires_explicit_one_shot_flag(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALLOW_EXCEPTIONAL_PIT_OOS_ONCE", raising=False)
    with pytest.raises(PermissionError, match="EXCEPTIONAL_PIT_OOS_DISABLED"):
        run(tmp_path)


def test_action_monthly_frame_reproduces_near_high_malus():
    dates=pd.bdate_range("2024-01-01",periods=320)
    close=pd.Series(range(100,420),index=dates,dtype=float)
    frame=pd.DataFrame({"Close":close})
    monthly=_action_monthly_frame("FRTEST","Technology",frame)
    latest=monthly.iloc[-1]
    assert latest["distance_high_52w_pct"] <= 2.0
    assert latest["high_52w_bonus_malus_points"] == -4.0
    assert latest["sector"] == "Technology"
