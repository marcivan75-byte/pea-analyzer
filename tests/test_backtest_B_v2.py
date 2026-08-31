"""Tests du backtest B v2 et de l'absence de fuite après sortie."""
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from v182.backtests.v21_8_1_backtest_B_v2 import compute_true_26w_pnl, detect_B_v2, run_backtest_B_v2


def test_stop_bloque():
    hist = pd.DataFrame({'open':[100,100,100], 'low':[99,90,101], 'high':[101,101,102], 'close':[100,91,102]})
    res = compute_true_26w_pnl(100, hist, 0.09)
    assert res['hit_stop'] is True and res['pnl'] == -0.09


def test_gap_through_stop_is_not_filled_at_theoretical_stop():
    hist = pd.DataFrame({'open':[85], 'low':[84], 'high':[88], 'close':[86]})
    res = compute_true_26w_pnl(100, hist, 0.09)
    assert res['hit_stop'] is True
    assert abs(res['pnl'] - (-0.15)) < 1e-12
    assert abs(res['exit_price'] - 85) < 1e-12


def test_vrai_pnl_sans_stop():
    hist = pd.DataFrame({'open':[100]*126, 'low':[95]*126, 'high':[110]*126, 'close':[105]*126})
    res = compute_true_26w_pnl(100, hist, 0.09)
    assert abs(res['pnl'] - 0.05) < 1e-6 and res['hit_stop'] is False
    assert res['day_stop'] == 126


def test_incomplete_horizon_without_stop_is_blocked():
    hist = pd.DataFrame({'open':[100,101,102], 'low':[95,96,97], 'high':[101,102,103], 'close':[100,101,102]})
    res = compute_true_26w_pnl(100, hist, 0.09)
    assert res['pnl'] is None
    assert res['block_reason'].startswith('BLOCK_DATA_INCOMPLETE_HORIZON_')


def test_ohlc_missing_or_inconsistent_is_blocked():
    missing_open=pd.DataFrame({'low':[95], 'high':[101], 'close':[100]})
    assert compute_true_26w_pnl(100, missing_open)['block_reason']=='BLOCK_DATA_OHLC_MISSING_open'
    bad=pd.DataFrame({'open':[102], 'low':[99], 'high':[101], 'close':[100]})
    assert compute_true_26w_pnl(100, bad)['block_reason']=='BLOCK_DATA_OHLC_INCONSISTENT'


def _single_daily(volume_tail):
    return pd.DataFrame({
        'date':pd.date_range('2025-01-01',periods=25,freq='B'),
        'close':[100.0]*25,
        'volume':[1e6]*20+volume_tail,
        'high':[101.0]*25,
        'low':[99.0]*25,
    })


def test_B1_vol_detection():
    df=_single_daily([4e6,4e6,1e6,1e6,1e6])
    df.loc[20,'close']=98; df.loc[20,'low']=97
    res=detect_B_v2(df)
    assert 'B1_vol' in res.columns


def test_B2_daily_J1():
    df=_single_daily([4e6,1e6,1e6,1e6,1e6])
    df.loc[20,'close']=98; df.loc[20,'low']=97
    res=detect_B_v2(df)
    assert bool(res['B2_daily'].iloc[21]) is True


def test_B_detection_requires_explicit_chronology():
    df=pd.DataFrame({'close':[100.0]*25,'volume':[1e6]*25,'high':[101.0]*25,'low':[99.0]*25})
    try:
        detect_B_v2(df)
        assert False, 'expected chronology block'
    except ValueError as e:
        assert 'explicit date or DatetimeIndex required' in str(e)


def test_B_detection_isolated_by_ticker():
    dates=pd.date_range('2025-01-01', periods=25, freq='B')
    a=pd.DataFrame({'date':dates,'ticker':'AAA','close':[100.0]*25,'volume':[1e6]*25,'high':[101.0]*25,'low':[99.0]*25})
    b=pd.DataFrame({'date':dates,'ticker':'BBB','close':[50.0]*25,'volume':[2e6]*25,'high':[51.0]*25,'low':[49.0]*25})
    a.loc[20,'volume']=6e6; a.loc[20,'close']=97.0; a.loc[20,'low']=96.0
    mixed=pd.concat([a,b], ignore_index=True)
    out=detect_B_v2(mixed)
    assert bool(out[out['ticker']=='AAA'].iloc[20]['B1_vol']) is True
    assert not out[out['ticker']=='BBB']['B1_vol'].any()
    assert not out[out['ticker']=='BBB']['B2_daily'].any()


def test_mae_mfe_logged():
    hist = pd.DataFrame({'open':[100,100,100], 'low':[90,95,100], 'high':[105,110,115], 'close':[91,108,112]})
    res = compute_true_26w_pnl(100, hist, 0.09)
    assert res['mae'] is not None and res['mfe'] is not None


def test_mae_mfe_stop_ignore_future_bars():
    hist = pd.DataFrame({'open':[100,100,100], 'low':[90,50,40], 'high':[103,160,180], 'close':[91,150,170]})
    res = compute_true_26w_pnl(100, hist, 0.09)
    assert res['day_stop'] == 1
    assert abs(res['mae'] - (-0.10)) < 1e-12
    assert abs(res['mfe'] - 0.03) < 1e-12


def test_backtest_price_path_is_isolated_by_ticker_and_future_date():
    signal_date=pd.Timestamp('2025-01-02')
    signals=pd.DataFrame([{'date':signal_date,'ticker':'AAA','close':100,'B_signal':True,'B_signal_type':'B1_VOL'}])
    dates=pd.date_range('2025-01-03', periods=126, freq='B')
    aaa=pd.DataFrame({'date':dates,'ticker':'AAA','open':100,'low':95,'high':110,'close':105})
    bbb=pd.DataFrame({'date':dates,'ticker':'BBB','open':50,'low':1,'high':60,'close':2})
    prices=pd.concat([bbb,aaa], ignore_index=True)
    out=run_backtest_B_v2(signals, prices, forward=126)
    assert len(out)==1
    assert out.iloc[0]['ticker']=='AAA'
    assert bool(out.iloc[0]['hit_stop']) is False
    assert abs(out.iloc[0]['pnl']-0.05)<1e-12
