import pandas as pd
from v182.hebdo.tabport_risk_sizing_development import POLICIES, learn_thresholds, assign_budget, objective


def _sample():
    return pd.DataFrame({
        'date':pd.to_datetime(['2020-01-01','2021-01-01','2022-01-01','2023-01-01'],utc=True),
        'ticker':['A','B','C','D'],'prob_stop_9':[0.10,0.20,0.30,0.99],
        'EV_net':[.1,.1,.1,.1],'tier':['CT_WATCH']*4,
    })


def test_policy_family_is_frozen_and_contains_baseline():
    assert POLICIES=={
        'BASELINE_4500':'BASELINE','HIGH_RISK_3750':'HIGH3750','HIGH_RISK_3000':'HIGH3000',
        'THREE_TIER_4500_3750_3000':'THREE','UPSIDE_5000_4500_3750':'UPSIDE'}


def test_thresholds_ignore_holdout():
    a=_sample(); t1=learn_thresholds(a)
    b=a.copy(); b.loc[b['date'].dt.year==2023,'prob_stop_9']=0.01
    assert t1==learn_thresholds(b)


def test_assign_budget_never_filters_and_baseline_is_exact():
    x=_sample(); t=learn_thresholds(x); y=assign_budget(x,'BASELINE',t)
    assert len(y)==len(x); assert (y['position_budget_eur']==4500).all()


def test_three_tier_budget_is_monotonic_with_risk():
    x=_sample().iloc[:3].copy(); t={'q33':0.15,'q60':0.20,'q67':0.25}; y=assign_budget(x,'THREE',t)
    assert list(y['position_budget_eur'])==[4500.0,3750.0,3000.0]


def test_objective_ignores_holdout_years():
    dev=pd.DataFrame({'periode':list(range(2011,2023)),'rendement_portefeuille_pct':[5.0]*12})
    mixed=pd.concat([dev,pd.DataFrame({'periode':[2023,2024,2025,2026],'rendement_portefeuille_pct':[999,-999,999,-999]})],ignore_index=True)
    s={'profit_factor':2.0,'rr_payoff':3.0,'drawdown_max_pct':-10.0}
    assert objective(dev,s)==objective(mixed,s)
