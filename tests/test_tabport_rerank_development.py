import pandas as pd

from v182.hebdo.tabport_rerank_development import CANDIDATES, _objective, rerank


def _sample():
    return pd.DataFrame({
        'date': pd.to_datetime(['2022-01-01']*3, utc=True),
        'ticker':['A','B','C'],
        'EV_net':[0.10,0.20,0.30],
        'j1_intraday_pct':[0.01,0.03,0.02],
        'prob_stop_9':[0.30,0.10,0.20],
        'vol_z':[1.0,3.0,2.0],
        'tier':['CT_WATCH','CT_WATCH','TCT'],
    })


def test_rerank_preserves_signal_universe_and_tiers():
    x=_sample(); y=rerank(x,CANDIDATES['EV_BALANCED'])
    assert len(y)==len(x)
    assert set(zip(y.ticker,y.date.astype(str)))==set(zip(x.ticker,x.date.astype(str)))
    assert dict(zip(y.ticker,y.tier))==dict(zip(x.ticker,x.tier))
    assert (y.EV_net>0).all()


def test_baseline_ev_preserves_original_order():
    x=_sample(); y=rerank(x,CANDIDATES['BASELINE_EV'])
    assert list(y.sort_values(['date','EV_net','ticker'],ascending=[True,False,True]).ticker)==['C','B','A']


def test_j1_and_risk_weights_reward_strong_confirmation_low_risk():
    x=_sample(); y=rerank(x,CANDIDATES['EV_J1_RISK']).set_index('ticker')
    # B has stronger J+1 and lower stop risk than A despite intermediate original EV.
    assert y.loc['B','EV_net'] > y.loc['A','EV_net']


def test_objective_ignores_holdout_rows():
    dev=pd.DataFrame({'periode':list(range(2011,2023)),'rendement_portefeuille_pct':[5.0]*12})
    mixed=pd.concat([dev,pd.DataFrame({'periode':[2023,2024,2025,2026],'rendement_portefeuille_pct':[-99,99,-99,99]})],ignore_index=True)
    assert _objective(dev)==_objective(mixed)


def test_candidate_family_frozen_and_small():
    assert set(CANDIDATES)=={'BASELINE_EV','EV_J1','EV_RISK','EV_J1_RISK','EV_J1_VOL','EV_BALANCED'}
    assert all(len(v)==4 for v in CANDIDATES.values())
