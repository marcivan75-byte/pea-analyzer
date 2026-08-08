import pandas as pd

from v182.decision.etf_decision_policy import score_etf, apply_etf_policy


def test_morningstar_4_star_bonus_exceeds_3_star_bonus():
    base = {
        "perf_1m_pct": 2,
        "perf_3m_pct": 8,
        "perf_6m_pct": 12,
        "perf_1y_pct": 20,
        "rsi14": 64,
        "macd_hist": 0.2,
        "max_drawdown_1y": -12,
        "volatility_60d": 18,
        "relative_strength": 2,
        "positive_reversal_flag": False,
    }
    score4, *_ = score_etf(pd.Series({**base, "morningstar_rating": 4}))
    score3, *_ = score_etf(pd.Series({**base, "morningstar_rating": 3}))
    assert score4 > score3


def test_high_risk_forces_review():
    row = pd.Series({
        "perf_1m_pct": 8,
        "perf_3m_pct": 20,
        "perf_6m_pct": 30,
        "perf_1y_pct": 45,
        "rsi14": 65,
        "macd_hist": 1.0,
        "max_drawdown_1y": -10,
        "volatility_60d": 20,
        "relative_strength": 10,
        "morningstar_rating": 4,
        "risk_indicator": 6,
        "positive_reversal_flag": True,
    })
    _, decision, execution, reason = score_etf(row)
    assert decision == "REVIEW"
    assert execution == "RESEARCH_ONLY"
    assert reason == "ETF_RISK_GATE"


def test_policy_writes_etf_decisions_and_appends_summary(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    pd.DataFrame([{
        "isin": "FR0000000001",
        "name": "ETF TEST",
        "yahoo_ticker": "TEST.PA",
        "perf_1m_pct": 3,
        "perf_3m_pct": 12,
        "perf_6m_pct": 18,
        "perf_1y_pct": 28,
        "rsi14": 63,
        "macd_hist": 0.4,
        "max_drawdown_1y": -12,
        "volatility_60d": 18,
        "relative_strength": 3,
        "morningstar_rating": 4,
        "positive_reversal_flag": True,
    }]).to_csv(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv", sep=";", index=False, encoding="utf-8-sig")
    (outputs / "V20.4_GITOK_COMMITTEE_SUMMARY.md").write_text("# Actions\n", encoding="utf-8")

    metrics = apply_etf_policy(tmp_path)
    assert metrics["rows"] == 1
    assert (outputs / "V20.4_GITOK_ETF_DECISIONS.csv").exists()
    text = (outputs / "V20.4_GITOK_COMMITTEE_SUMMARY.md").read_text(encoding="utf-8")
    assert "ETF PEA" in text
