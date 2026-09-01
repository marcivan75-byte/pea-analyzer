import pandas as pd

from v182.hebdo.tabport_adaptive_stop_publish import AdaptiveRunner, _attribution


def test_constant_stop_risk_reduces_notional_when_stop_is_wider():
    signals = pd.DataFrame([
        {"date": "2025-01-01", "ticker": "AAA", "EV_net": 1.0, "tier": "TCT", "atr_14_pct": 0.06}
    ])
    prices = pd.DataFrame([
        {"date": "2025-01-02", "ticker": "AAA", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}
    ])
    fixed = AdaptiveRunner("ATR2_5_CAP15").run(signals, prices)["ledger"].iloc[0]
    parity = AdaptiveRunner("ATR2_5_CAP15", risk_parity=True).run(signals, prices)["ledger"].iloc[0]
    assert fixed["stop_pct_signal"] == parity["stop_pct_signal"] == 0.15
    assert parity["shares"] < fixed["shares"]
    assert parity["cash_invested"] < fixed["cash_invested"]
    assert parity["sizing_policy"] == "CONSTANT_STOP_RISK"


def test_attribution_detects_single_trade_dependency():
    base = pd.DataFrame([
        {"ticker": "AAA", "signal_date": "2025-01-01", "entry_date": "2025-01-02", "exit_date": "2025-02-01",
         "pnl_net": 100.0, "return_net": 0.1, "exit_reason": "TIME_26W", "mae": -0.02, "mfe": 0.2, "stop_pct_signal": 0.09}
    ])
    candidate = pd.concat([base, pd.DataFrame([
        {"ticker": "BIG", "signal_date": "2025-03-01", "entry_date": "2025-03-02", "exit_date": "2025-04-01",
         "pnl_net": 500.0, "return_net": 0.5, "exit_reason": "TIME_26W", "mae": -0.02, "mfe": 0.6, "stop_pct_signal": 0.15}
    ])], ignore_index=True)
    _, stats = _attribution(base, candidate)
    assert stats["candidate_only_trades"] == 1
    assert stats["total_pnl_delta_eur"] == 500.0
    assert stats["delta_excluding_top_candidate_only_trade_eur"] == 0.0
    assert stats["robust_without_top_candidate_only_trade"] is False
