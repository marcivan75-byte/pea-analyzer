import json
from pathlib import Path

import pandas as pd

from v182.backtest.etf_grok2_research_backtest import _simulate_grok2_exit
from v182.features.etf_grok2_cdc import _adjusted_weights

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'config' / 'V20.8_ETF_GROK_HIGH_PRECISION.json').read_text())
G2 = json.loads((ROOT / 'config' / 'ETF_GROK2_CDC_V1.json').read_text())


def test_adjusted_weights_remain_normalized_and_deemphasize_momentum():
    w = _adjusted_weights(BASE, G2)
    assert len(w) == 38
    assert abs(sum(w.values()) - 1.0) < 1e-12
    # The CDC design deliberately reduces momentum relative to risk-adjusted/liquidity groups.
    assert G2['quantitative_core']['group_multipliers']['Momentum long'] < G2['quantitative_core']['group_multipliers']['Risque ajusté']


def test_thesis_break_requires_minimum_holding_and_broken_long_trend():
    idx = pd.bdate_range('2023-01-02', periods=500)
    # Long rise followed by sustained decline; after 63 sessions the 6m return eventually turns non-positive below SMA200.
    close = list(100 + pd.Series(range(260)) * 0.1) + list(126 - pd.Series(range(240)) * 0.25)
    frame = pd.DataFrame({'Close': close}, index=idx)
    entry_date = idx[260]
    entry_price = float(frame.loc[entry_date, 'Close'])
    d, px, hold, reason = _simulate_grok2_exit(frame, entry_date, entry_price, G2)
    assert hold >= G2['exit_policy']['minimum_holding_before_thesis_break_sessions']
    assert reason in {'THESIS_BREAK_TREND', 'HARD_RISK_STOP', 'HORIZON_REVIEW_CLOSE'}


def test_small_correction_alone_does_not_trigger_take_profit_or_price_stop():
    idx = pd.bdate_range('2024-01-02', periods=120)
    close = [100.0 + 0.03 * i for i in range(60)] + [101.8 - 0.08 * (i-60) for i in range(60,120)]
    frame = pd.DataFrame({'Close': close}, index=idx)
    d, px, hold, reason = _simulate_grok2_exit(frame, idx[0], 100.0, G2)
    assert reason == 'END_OF_DATA'
