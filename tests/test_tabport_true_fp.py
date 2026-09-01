import pandas as pd

from v182.hebdo.tabport_walkforward import attach_mature_outcomes


def test_true_fp_label_separates_never_recovered_from_recovered_stop():
    dates=pd.bdate_range('2025-01-02',periods=126,tz='UTC'); rows=[]
    for ticker,recover in [('BAD',False),('SHAKE',True)]:
        for i,d in enumerate(dates):
            close=100.0
            if i==5: close=90.0
            elif i>5: close=105.0 if recover and i>=20 else 92.0
            rows.append({'date':d,'ticker':ticker,'open':close,'high':close*1.01,'low':close*.99,'close':close})
    candidates=pd.DataFrame([{'date':'2025-01-01','ticker':'BAD'},{'date':'2025-01-01','ticker':'SHAKE'}])
    out=attach_mature_outcomes(candidates,pd.DataFrame(rows)).set_index('ticker')
    assert int(out.loc['BAD','true_fp_durable'])==1
    assert int(out.loc['SHAKE','true_fp_durable'])==0 and bool(out.loc['SHAKE','recovered_entry_after_stop']) is True
