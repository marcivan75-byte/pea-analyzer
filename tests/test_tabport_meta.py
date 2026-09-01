import numpy as np
import pandas as pd

from v182.hebdo.tabport import Tabport65k, TabportConfig


def prices_for(tickers, dates, open_px=100.0, low_px=99.0, high_px=102.0, close_px=101.0):
    rows=[]
    for d in dates:
        for t in tickers:
            rows.append({'date':d,'ticker':t,'open':open_px,'low':low_px,'high':high_px,'close':close_px})
    return pd.DataFrame(rows)


def test_position_budget_whole_shares_fees_and_slippage():
    sig=pd.DataFrame([{'date':'2025-01-01','ticker':'AAA','EV_net':0.10,'tier':'CT_WATCH'}])
    px=prices_for(['AAA'],pd.to_datetime(['2025-01-02','2025-01-03']))
    out=Tabport65k(TabportConfig(max_hold_sessions=2)).run(sig,px)
    trade=out['ledger'].iloc[0]
    assert trade['shares']==44
    assert trade['cash_invested']<=4500.0
    assert abs(trade['entry_price']-100.1)<1e-12
    assert trade['fees_total']>0
    assert trade['slippage_rate_side']==0.001


def test_ev_priority_and_max_positions():
    tickers=['A','B','C']
    sig=pd.DataFrame([
        {'date':'2025-01-01','ticker':'A','EV_net':0.03},
        {'date':'2025-01-01','ticker':'B','EV_net':0.09},
        {'date':'2025-01-01','ticker':'C','EV_net':0.06},
    ])
    px=prices_for(tickers,pd.to_datetime(['2025-01-02','2025-01-03']))
    cfg=TabportConfig(max_positions=2,max_entries_month=10,max_hold_sessions=2)
    out=Tabport65k(cfg).run(sig,px)
    assert set(out['ledger']['ticker'])=={'B','C'}
    skipped=out['skipped']
    assert ((skipped['ticker']=='A') & (skipped['reason']=='MAX_POSITIONS')).any()


def test_monthly_entry_limit_five():
    tickers=[f'T{i}' for i in range(6)]
    sig=pd.DataFrame([{'date':'2025-01-01','ticker':t,'EV_net':1-i/10} for i,t in enumerate(tickers)])
    px=prices_for(tickers,pd.to_datetime(['2025-01-02','2025-01-03']))
    cfg=TabportConfig(max_positions=12,max_entries_month=5,max_hold_sessions=2)
    out=Tabport65k(cfg).run(sig,px)
    assert len(out['ledger'])==5
    assert (out['skipped']['reason']=='MAX_ENTRIES_MONTH').sum()==1


def test_yearly_entry_limit():
    tickers=['A','B','C']
    sig=pd.DataFrame([{'date':'2025-01-01','ticker':t,'EV_net':0.1} for t in tickers])
    px=prices_for(tickers,pd.to_datetime(['2025-01-02','2025-01-03']))
    cfg=TabportConfig(max_entries_year=2,max_entries_month=10,max_hold_sessions=2)
    out=Tabport65k(cfg).run(sig,px)
    assert len(out['ledger'])==2
    assert (out['skipped']['reason']=='MAX_ENTRIES_YEAR').sum()==1


def test_entry_day_stop_is_applied():
    sig=pd.DataFrame([{'date':'2025-01-01','ticker':'AAA','EV_net':0.1}])
    px=pd.DataFrame([
        {'date':'2025-01-02','ticker':'AAA','open':100,'high':101,'low':90,'close':95},
        {'date':'2025-01-03','ticker':'AAA','open':95,'high':97,'low':94,'close':96},
    ])
    out=Tabport65k().run(sig,px)
    trade=out['ledger'].iloc[0]
    assert trade['entry_date']==pd.Timestamp('2025-01-02',tz='UTC')
    assert trade['exit_date']==trade['entry_date']
    assert trade['sessions_held']==1
    assert trade['exit_reason']=='STOP_-9%'
    assert trade['mae'] < -0.09


def test_gap_through_stop_uses_open_not_theoretical_stop():
    sig=pd.DataFrame([{'date':'2025-01-01','ticker':'AAA','EV_net':0.1}])
    px=pd.DataFrame([
        {'date':'2025-01-02','ticker':'AAA','open':100,'high':102,'low':99,'close':101},
        {'date':'2025-01-03','ticker':'AAA','open':85,'high':88,'low':84,'close':86},
    ])
    out=Tabport65k().run(sig,px)
    trade=out['ledger'].iloc[0]
    assert trade['exit_reason']=='STOP_GAP_THROUGH'
    expected=85*(1-0.001)
    assert abs(trade['exit_price']-expected)<1e-12


def test_reentry_after_exit_allowed_but_no_simultaneous_duplicate():
    sig=pd.DataFrame([
        {'date':'2025-01-01','ticker':'AAA','EV_net':0.10},
        {'date':'2025-01-02','ticker':'AAA','EV_net':0.09},
        {'date':'2025-01-04','ticker':'AAA','EV_net':0.08},
    ])
    px=pd.DataFrame([
        {'date':'2025-01-02','ticker':'AAA','open':100,'high':101,'low':90,'close':95},
        {'date':'2025-01-03','ticker':'AAA','open':95,'high':97,'low':94,'close':96},
        {'date':'2025-01-05','ticker':'AAA','open':100,'high':103,'low':99,'close':102},
        {'date':'2025-01-06','ticker':'AAA','open':102,'high':104,'low':101,'close':103},
    ])
    out=Tabport65k(TabportConfig(max_hold_sessions=2,max_entries_month=10)).run(sig,px)
    # Premier trade stoppé le 02; signal du 02 entre le 03; signal du 04 peut ré-entrer après sa sortie.
    assert len(out['ledger'])==3
    assert list(out['ledger']['entry_date'])==[
        pd.Timestamp('2025-01-02',tz='UTC'),pd.Timestamp('2025-01-03',tz='UTC'),pd.Timestamp('2025-01-05',tz='UTC')]


def test_time_exit_counts_entry_session():
    sig=pd.DataFrame([{'date':'2025-01-01','ticker':'AAA','EV_net':0.1}])
    px=prices_for(['AAA'],pd.to_datetime(['2025-01-02','2025-01-03','2025-01-06']))
    out=Tabport65k(TabportConfig(max_hold_sessions=2)).run(sig,px)
    trade=out['ledger'].iloc[0]
    assert trade['exit_reason']=='TIME_26W'
    assert trade['exit_date']==pd.Timestamp('2025-01-03',tz='UTC')
    assert trade['sessions_held']==2


def test_negative_ev_and_disallowed_tier_not_entered():
    sig=pd.DataFrame([
        {'date':'2025-01-01','ticker':'A','EV_net':-0.01,'tier':'CT_WATCH'},
        {'date':'2025-01-01','ticker':'B','EV_net':0.05,'tier':'EXCLU'},
    ])
    px=prices_for(['A','B'],pd.to_datetime(['2025-01-02','2025-01-03']))
    try:
        Tabport65k().run(sig,px)
        assert False
    except ValueError as e:
        assert 'no eligible non-negative-EV signals' in str(e)


def test_duplicate_signal_and_bad_ohlc_fail_closed():
    dup=pd.DataFrame([
        {'date':'2025-01-01','ticker':'A','EV_net':0.1},
        {'date':'2025-01-01','ticker':'A','EV_net':0.2},
    ])
    px=prices_for(['A'],pd.to_datetime(['2025-01-02']))
    try:
        Tabport65k().run(dup,px); assert False
    except ValueError as e:
        assert 'duplicate ticker signal' in str(e)
    sig=pd.DataFrame([{'date':'2025-01-01','ticker':'A','EV_net':0.1}])
    bad=pd.DataFrame([{'date':'2025-01-02','ticker':'A','open':100,'high':99,'low':98,'close':99}])
    try:
        Tabport65k().run(sig,bad); assert False
    except ValueError as e:
        assert 'invalid OHLC' in str(e)


def test_quarterly_returns_chain_from_previous_period_end():
    sig=pd.DataFrame([
        {'date':'2025-03-30','ticker':'A','EV_net':0.1},
        {'date':'2025-04-02','ticker':'B','EV_net':0.1},
    ])
    px=pd.DataFrame([
        {'date':'2025-03-31','ticker':'A','open':100,'high':102,'low':99,'close':101},
        {'date':'2025-04-01','ticker':'A','open':101,'high':103,'low':100,'close':102},
        {'date':'2025-04-03','ticker':'B','open':100,'high':102,'low':99,'close':101},
        {'date':'2025-04-04','ticker':'B','open':101,'high':103,'low':100,'close':102},
    ])
    out=Tabport65k(TabportConfig(max_hold_sessions=2,max_entries_month=10)).run(sig,px)
    q=out['quarterly']
    assert len(q)==2
    assert abs(q.iloc[0]['start_equity']-65000)<1e-9
    assert abs(q.iloc[1]['start_equity']-q.iloc[0]['end_equity'])<1e-9
