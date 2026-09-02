import pandas as pd

from v182.hebdo.tabport import TabportConfig
from v182.hebdo.tabport_hold_horizon_development import HORIZONS, MAX_HORIZON, _candidate_config, common_mature_cohort
from v182.hebdo.tabport_rerank_development import _objective


def _bars(n):
    dates=pd.bdate_range('2020-01-02',periods=n+1,tz='UTC')
    return pd.DataFrame({'date':dates,'ticker':['A']*len(dates),'open':100.0,'high':101.0,'low':99.0,'close':100.0,'volume':1000.0})


def test_horizon_family_is_small_and_frozen():
    assert HORIZONS=={'H63':63,'H126_BASELINE':126,'H189':189,'H252':252}
    assert MAX_HORIZON==252


def test_common_maturity_accepts_exact_252_future_bars():
    bars=_bars(252)
    sig=pd.DataFrame([{'date':bars.iloc[0].date,'ticker':'A','EV_net':0.1}])
    out,audit=common_mature_cohort(sig,bars,252)
    assert len(out)==1
    assert audit['signals_common_mature']==1
    assert out.iloc[0].future_bars_after_confirmation==252


def test_common_maturity_rejects_less_than_252_future_bars():
    bars=_bars(251)
    sig=pd.DataFrame([{'date':bars.iloc[0].date,'ticker':'A','EV_net':0.1}])
    try:
        common_mature_cohort(sig,bars,252)
        assert False
    except ValueError as exc:
        assert 'NO_COMMON_MATURE_SIGNALS' in str(exc)


def test_candidate_config_changes_only_horizon():
    base=TabportConfig()
    changed=_candidate_config(base,189)
    assert changed.max_hold_sessions==189
    for name in base.__dataclass_fields__:
        if name!='max_hold_sessions':
            assert getattr(changed,name)==getattr(base,name)


def test_development_objective_ignores_holdout():
    dev=pd.DataFrame({'periode':list(range(2011,2023)),'rendement_portefeuille_pct':[4.0]*12})
    mixed=pd.concat([dev,pd.DataFrame({'periode':[2023,2024,2025,2026],'rendement_portefeuille_pct':[999,-999,999,-999]})],ignore_index=True)
    assert _objective(dev)==_objective(mixed)
