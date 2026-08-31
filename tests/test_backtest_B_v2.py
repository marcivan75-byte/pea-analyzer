"""
tests/test_backtest_B_v2.py
V22.5 AUDIT 5/5 - 5 tests PIT fail-closed + B2 daily + stop + MAE/MFE
"""
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from v182.backtests.v21_8_1_backtest_B_v2 import compute_true_26w_pnl, detect_B_v2

def test_stop_bloque():
    entry=100
    hist=pd.DataFrame({'low':[99,90,101],'high':[101,101,102],'close':[100,91,102]})
    res=compute_true_26w_pnl(entry, hist, 0.09)
    assert res['hit_stop']==True and res['pnl']==-0.09

def test_vrai_pnl_sans_stop():
    entry=100
    hist=pd.DataFrame({'low':[95]*126,'high':[110]*126,'close':[105]*126})
    res=compute_true_26w_pnl(entry, hist, 0.09)
    assert abs(res['pnl']-0.05)<1e-6 and res['hit_stop']==False

def test_B1_vol_detection():
    df=pd.DataFrame({'close':[100]*25, 'volume':[1e6]*20+[4e6,4e6,1e6,1e6,1e6], 'high':[101]*25, 'low':[99]*25})
    df.loc[20,'close']=98
    df['close']=df['close'].astype(float)
    res=detect_B_v2(df)
    assert 'B1_vol' in res.columns

def test_B2_daily_J1():
    df=pd.DataFrame({'close':[100]*25, 'volume':[1e6]*20+[4e6,1e6,1e6,1e6,1e6], 'high':[101]*25, 'low':[99]*25})
    df.loc[20,'close']=98
    res=detect_B_v2(df)
    assert res['B2_daily'].iloc[21]==True

def test_mae_mfe_logged():
    entry=100
    hist=pd.DataFrame({'low':[90,95,100],'high':[105,110,115],'close':[102,108,112]})
    res=compute_true_26w_pnl(entry, hist, 0.09)
    assert res['mae'] is not None and res['mfe'] is not None

if __name__=="__main__":
    test_stop_bloque(); test_vrai_pnl_sans_stop(); test_B1_vol_detection(); test_B2_daily_J1(); test_mae_mfe_logged()
    print("5 tests PASS")
