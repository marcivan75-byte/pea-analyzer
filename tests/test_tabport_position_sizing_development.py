import pandas as pd

from v182.hebdo.tabport import TabportConfig
from v182.hebdo.tabport_position_sizing_development import SIZES_EUR, candidate_config, sizing_objective


def test_size_family_is_frozen_and_contains_baseline():
    assert SIZES_EUR == {
        'SIZE_3000': 3000.0,
        'SIZE_3750': 3750.0,
        'SIZE_4500_BASELINE': 4500.0,
        'SIZE_5000': 5000.0,
        'SIZE_5400': 5400.0,
    }


def test_candidate_config_changes_only_position_budget():
    base=TabportConfig(); changed=candidate_config(base,5000.0)
    assert changed.max_position_eur==5000.0
    for name in base.__dataclass_fields__:
        if name!='max_position_eur':
            assert getattr(changed,name)==getattr(base,name)


def test_invalid_position_budget_blocked():
    base=TabportConfig()
    for x in [0,-1,float('nan'),70000]:
        try:
            candidate_config(base,x)
            assert False
        except ValueError as exc:
            assert 'INVALID_POSITION_BUDGET' in str(exc)


def test_sizing_objective_ignores_holdout_years():
    dev=pd.DataFrame({'periode':list(range(2011,2023)),'rendement_portefeuille_pct':[5.0]*12})
    mixed=pd.concat([dev,pd.DataFrame({'periode':[2023,2024,2025,2026],'rendement_portefeuille_pct':[999,-999,999,-999]})],ignore_index=True)
    s={'drawdown_max_pct':-10.0}
    assert sizing_objective(dev,s)==sizing_objective(mixed,s)


def test_sizing_objective_penalizes_larger_drawdown():
    y=pd.DataFrame({'periode':list(range(2011,2023)),'rendement_portefeuille_pct':[5.0]*12})
    assert sizing_objective(y,{'drawdown_max_pct':-5.0}) > sizing_objective(y,{'drawdown_max_pct':-20.0})
