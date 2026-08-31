import pandas as pd
import numpy as np

from v182.hebdo.confirmation_entry import ConfirmationEntry
from v182.hebdo.fp_early_exit import FPEarlyExit
from v182.hebdo.meta_labeler import MetaLabeler
from v182.hebdo.hebdo_at_meta import HebdoATMeta


def make_features(n=100):
    return pd.DataFrame({
        'ticker':[f'T{i}' for i in range(n)],
        'close':np.linspace(20,120,n),
        'vol_z':np.linspace(-1,3,n),
        'drawdown_4w':np.linspace(-0.01,-0.10,n),
        'atr_14_pct':np.linspace(0.02,0.05,n),
        'mom_26w_sector':np.linspace(-1,2,n),
        'roe':0.10,
        'debt_to_equity':0.8,
        'sma200':50,
        'adv_20m_eur':2_000_000,
        'days_to_earnings':30,
        'rsi_14_hebdo':55,
    })


def test_meta_pipeline_runs_and_ranks():
    out=HebdoATMeta().run(make_features())
    assert len(out)>0
    assert {'EV_net','tier','prob_stop_9','prob_meta','META_STATUS'}.issubset(out.columns)
    assert out['EV_net'].is_monotonic_decreasing


def test_meta_pipeline_fail_closed_missing_feature():
    df=make_features().drop(columns=['atr_14_pct'])
    try:
        HebdoATMeta().run(df)
        assert False, 'expected BLOCK_DATA_META'
    except ValueError as e:
        assert 'BLOCK_DATA_META' in str(e)


def test_confirmation_uses_requested_ticker():
    friday=pd.DataFrame([{'ticker':'ABC','close':100}])
    bars=pd.DataFrame([{'ticker':'ABC','open':100,'close':101,'vol_z':1}])
    out=ConfirmationEntry().filter_batch_j1(friday,bars)
    assert len(out)==1 and bool(out.iloc[0]['enter_confirmed']) is True


def test_fail_fast_only_below_minus_2_5pct():
    ex=FPEarlyExit()
    assert ex.check_exit(100, {'close':100.5,'low':100}, 2)[0] is False
    hit=ex.check_exit(100, {'close':97,'low':96.5}, 2)
    assert hit[0] is True and hit[1].startswith('FAIL_FAST_J2')


def test_meta_training_sparse_classes_blocks_safely():
    df=make_features(20)
    df['meta_label']=1
    result=MetaLabeler().train(df)
    assert result['status'].startswith('BLOCK_')
