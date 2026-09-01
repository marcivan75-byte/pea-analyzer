import pandas as pd

from v182.hebdo.tabport_convex_exit_publish import ConvexExitRunner


def test_convex_exit_extends_only_large_winner():
    dates=pd.bdate_range('2025-01-02',periods=189,tz='UTC')
    rows=[]
    for i,d in enumerate(dates):
        close=100+25*min(i,125)/125 if i<126 else 125+75*(i-125)/63
        rows.append({'date':d,'ticker':'AAA','open':close,'high':close*1.005,'low':close*.995,'close':close})
    prices=pd.DataFrame(rows); signals=pd.DataFrame([{'date':'2025-01-01','ticker':'AAA','EV_net':1.0,'tier':'TCT'}])
    base=ConvexExitRunner(False).run(signals,prices)['ledger'].iloc[0]
    convex=ConvexExitRunner(True).run(signals,prices)['ledger'].iloc[0]
    assert base['sessions_held']==126 and base['exit_reason']=='TIME_26W'
    assert convex['sessions_held']==189 and convex['exit_reason']=='TIME_39W_CONVEX'
    assert convex['return_net']>base['return_net']
    assert bool(convex['extension_activated']) is True
