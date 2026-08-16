import pandas as pd
import numpy as np

from v182.backtest.action_entry_exit_horizons_v21_8 import _score_cross_section, _path, CT_WEIGHTS


def test_cross_section_score_prefers_stronger_high_direction_fields():
    frame=pd.DataFrame({"rsi14":[30,70],"macd_hist":[-1,1],"rvol20":[0.8,2.0],"perf_1m":[-0.1,0.1],"perf_3m":[-0.2,0.2],"vol20":[0.4,0.2],"max_dd_1y":[-0.4,-0.1],"perf_6m":[-0.2,0.3],"volume":[100,1000],"relative_strength":[-0.1,0.1],"positive_reversal":[0,1],"stoch_bull_cross":[0,1]},index=["A","B"])
    scores=_score_cross_section(frame,CT_WEIGHTS)
    assert scores["B"]>scores["A"]


def test_path_has_no_take_profit_or_stop_execution():
    idx=pd.bdate_range("2024-01-01",periods=80)
    close=pd.Series(np.linspace(100,130,len(idx)),index=idx)
    f=pd.DataFrame({"close":close,"ret_5d":close.pct_change(5),"ret_21d":close.pct_change(21),"dist_sma50":0.01,"dist_sma200":0.05,"slope_sma50_20d":0.02,"drawdown_63d":0.0,"vol20":0.2},index=idx)
    out=_path(f,pd.Timestamp("2024-01-05"),"CT",1)
    assert out is not None
    assert out["mfe"]>0.20
    assert out["final_return"]>0.20
