import pandas as pd

from v182.hebdo.tabport_enriched import overall_summary, period_table


def test_enriched_metrics_pf_rr_mae_mfe_and_periods():
    ledger = pd.DataFrame([
        {"exit_date":"2025-02-01","return_net":0.10,"pnl_net":400.0,"fees_total":12.0,"exit_reason":"TIME_26W","mae":-0.03,"mfe":0.15,"sessions_held":20},
        {"exit_date":"2025-03-01","return_net":-0.05,"pnl_net":-200.0,"fees_total":11.0,"exit_reason":"STOP_-9%","mae":-0.09,"mfe":0.02,"sessions_held":10},
        {"exit_date":"2025-05-01","return_net":0.05,"pnl_net":200.0,"fees_total":10.0,"exit_reason":"TIME_26W","mae":-0.02,"mfe":0.09,"sessions_held":15},
    ])
    nav = pd.DataFrame([
        {"date":"2025-01-01","equity":65000.0},
        {"date":"2025-03-31","equity":65200.0},
        {"date":"2025-06-30","equity":65400.0},
    ])
    s = overall_summary(ledger, nav)
    assert s["trades"] == 3
    assert s["gains"] == 2
    assert s["pertes_faux_positifs"] == 1
    assert round(s["profit_factor"], 6) == 3.0
    assert round(s["rr_payoff"], 6) == 1.5
    assert s["stops"] == 1
    q = period_table(ledger, nav, "Q")
    assert set(q["periode"]) == {"2025Q1", "2025Q2"}
