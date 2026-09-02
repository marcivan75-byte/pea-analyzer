import pandas as pd

from v182.hebdo.tabport_decisive_benchmark import decision, top_n_per_signal_date, score_vs_benchmark


def test_top_n_per_signal_date_keeps_best_ev():
    s=pd.DataFrame({
        'date':pd.to_datetime(['2020-01-01','2020-01-01','2020-01-01','2020-01-08'],utc=True),
        'ticker':['B','A','C','D'],
        'EV_net':[1.0,3.0,2.0,4.0],
    })
    x=top_n_per_signal_date(s,2)
    assert x[x['date']==pd.Timestamp('2020-01-01',tz='UTC')]['ticker'].tolist()==['A','C']


def test_decision_requires_material_economic_edge():
    b={'cagr':0.10,'max_drawdown':-0.20}
    assert decision({'cagr':0.131,'max_drawdown':-0.21},b)['qualifies'] is True
    assert decision({'cagr':0.101,'max_drawdown':-0.14},b)['qualifies'] is True
    assert decision({'cagr':0.11,'max_drawdown':-0.19},b)['qualifies'] is False


def test_dev_objective_rewards_cagr_and_drawdown():
    b={'cagr':0.10,'max_drawdown':-0.20}
    a={'cagr':0.12,'max_drawdown':-0.15}
    c={'cagr':0.11,'max_drawdown':-0.20}
    assert score_vs_benchmark(a,b)>score_vs_benchmark(c,b)
