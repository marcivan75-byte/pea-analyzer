import pandas as pd
from v182.hebdo.tabport import TabportConfig
from v182.hebdo.tabport_stop_sensitivity_development import STOPS,candidate_config,objective

def test_stop_family_frozen_and_has_baseline():
    assert STOPS=={'STOP_7':0.07,'STOP_8':0.08,'STOP_9_BASELINE':0.09,'STOP_10':0.10,'STOP_11':0.11}

def test_candidate_changes_only_stop():
    b=TabportConfig(); c=candidate_config(b,0.08)
    assert c.stop_pct==0.08
    for n in b.__dataclass_fields__:
        if n!='stop_pct': assert getattr(c,n)==getattr(b,n)

def test_invalid_stops_blocked():
    b=TabportConfig()
    for x in [0,0.01,0.25,float('nan')]:
        try: candidate_config(b,x); assert False
        except ValueError as e: assert 'BLOCK_STOP_INVALID' in str(e)

def test_objective_ignores_holdout_years():
    dev=pd.DataFrame({'periode':list(range(2011,2023)),'rendement_portefeuille_pct':[5.0]*12})
    mixed=pd.concat([dev,pd.DataFrame({'periode':[2023,2024,2025,2026],'rendement_portefeuille_pct':[999,-999,999,-999]})],ignore_index=True)
    s={'drawdown_max_pct':-10,'profit_factor':2,'rr_payoff':2.5,'stop_rate_pct':50}
    assert objective(dev,s)==objective(mixed,s)
