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
    assert G2['quantitative_core']['group_multipliers']['Momentum long'] < G2['quantitative_core']['group_multipliers']['Risque ajusté']
    assert G2['quantitative_core']['group_multipliers']['Momentum court'] < G2['quantitative_core']['group_multipliers']['Liquidité']


def test_v2_uses_proven_v1_replay_exit_after_rejected_thesis_proxy():
    assert G2['exit_policy']['mode'] == 'PROVEN_V1_REPLAY'
    assert G2['rejected_iteration']['reason'] == 'THESIS_PROXY_EXIT_REDUCED_WIN_RATE_AND_DEGRADED_2024_2026'
    assert G2['design_principles']['unvalidated_thesis_exit_not_promoted'] is True


def test_proven_replay_target_exit_is_plus_four_percent_on_close():
    idx = pd.bdate_range('2026-01-02', periods=4)
    frame = pd.DataFrame({'Close': [100.0, 102.0, 104.0, 105.0]}, index=idx)
    d, px, hold, reason = _simulate_grok2_exit(frame, idx[0], 100.0, G2)
    assert reason == 'TARGET_CLOSE'
    assert px == 104.0
    assert hold == 2


def test_historical_static_2026_backfill_is_forbidden():
    assert G2['governance']['historical_static_2026_backfill_forbidden'] is True
    assert G2['operational_cdc_overlay']['static_overlay_can_create_historical_signal'] is False
