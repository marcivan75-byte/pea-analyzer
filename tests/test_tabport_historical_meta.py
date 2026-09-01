import json
from pathlib import Path

import pandas as pd
import pytest

from v182.hebdo.tabport import TabportConfig
from v182.hebdo.tabport_historical import run_historical


def _write_inputs(tmp_path: Path):
    signals = pd.DataFrame([
        {'date':'2025-01-01','ticker':'AAA','EV_net':0.10,'tier':'CT_WATCH',
         'pit_snapshot_time':'2024-12-31T20:00:00+01:00'},
    ])
    prices = pd.DataFrame([
        {'date':'2025-01-01','ticker':'AAA','open':100,'high':101,'low':99,'close':100},
        {'date':'2025-01-02','ticker':'AAA','open':100,'high':102,'low':99,'close':101},
        {'date':'2025-01-03','ticker':'AAA','open':101,'high':103,'low':100,'close':102},
    ])
    s = tmp_path/'signals.csv'; p = tmp_path/'ohlc.csv'
    signals.to_csv(s,index=False); prices.to_csv(p,index=False)
    return s,p


def test_historical_runner_writes_provenance_and_no_synthetic_fallback(tmp_path):
    s,p = _write_inputs(tmp_path)
    out = tmp_path/'out'
    res = run_historical(s,p,'2025-01-01','2025-01-03',out,TabportConfig(max_hold_sessions=2))
    manifest = json.loads((out/'TABPORT_MANIFEST.json').read_text())
    assert manifest['status']=='OK'
    assert manifest['synthetic_fallback'] is False
    assert manifest['retuning'] is False
    assert manifest['inputs']['signals']['pit_validation']=='ROW_LEVEL_T_MINUS_1_22H_EUROPE_PARIS'
    assert len(manifest['inputs']['signals']['sha256'])==64
    assert len(manifest['inputs']['ohlc']['sha256'])==64
    assert (out/'TABPORT_LEDGER.csv').exists()
    assert (out/'TABPORT_DAILY_NAV.csv').exists()
    assert len(res['ledger'])==1


def test_historical_runner_blocks_when_ohlc_do_not_span_signal_j1(tmp_path):
    s,_ = _write_inputs(tmp_path)
    bad = tmp_path/'bad.csv'
    pd.DataFrame([
        {'date':'2025-01-01','ticker':'AAA','open':100,'high':102,'low':99,'close':101},
    ]).to_csv(bad,index=False)
    with pytest.raises(ValueError,match='OHLC do not span'):
        run_historical(s,bad,'2025-01-01','2025-01-03',tmp_path/'out')


def test_historical_runner_blocks_missing_input(tmp_path):
    _,p = _write_inputs(tmp_path)
    with pytest.raises(ValueError,match='missing/empty input'):
        run_historical(tmp_path/'missing.csv',p,'2025-01-01','2025-01-03',tmp_path/'out')


def test_historical_runner_requires_row_level_pit(tmp_path):
    s,p = _write_inputs(tmp_path)
    df=pd.read_csv(s).drop(columns=['pit_snapshot_time']); df.to_csv(s,index=False)
    with pytest.raises(ValueError,match='missing pit_snapshot_time'):
        run_historical(s,p,'2025-01-01','2025-01-03',tmp_path/'out')


def test_historical_runner_blocks_pit_lookahead(tmp_path):
    s,p = _write_inputs(tmp_path)
    df=pd.read_csv(s); df.loc[0,'pit_snapshot_time']='2025-01-01T00:00:00+01:00'; df.to_csv(s,index=False)
    with pytest.raises(ValueError,match='PIT look-ahead'):
        run_historical(s,p,'2025-01-01','2025-01-03',tmp_path/'out')
