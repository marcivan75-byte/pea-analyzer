from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]


def _num(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(result) else result


def _bool(value) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "oui"}


def score_etf(row: pd.Series) -> tuple[float, str, str, str]:
    """Return (score, decision, execution, reason) for a PEA ETF.

    The score is deliberately transparent and uses only observed fields already
    present in the V18.2 ETF enriched master. Morningstar 4* receives a strong
    bonus, 3* a smaller bonus, and high risk indicators are penalised. No live
    brokerage/order execution is ever enabled.
    """
    perf_1m = _num(row.get("perf_1m_pct")) or 0.0
    perf_3m = _num(row.get("perf_3m_pct")) or 0.0
    perf_6m = _num(row.get("perf_6m_pct")) or 0.0
    perf_1y = _num(row.get("perf_1y_pct")) or 0.0
    rsi = _num(row.get("rsi14"))
    macd_hist = _num(row.get("macd_hist")) or 0.0
    drawdown = _num(row.get("max_drawdown_1y"))
    volatility = _num(row.get("volatility_60d"))
    relative_strength = _num(row.get("relative_strength")) or 0.0
    rating = _num(row.get("morningstar_rating"))
    risk = _num(row.get("risk_indicator"))
    reversal = _bool(row.get("positive_reversal_flag"))

    score = 50.0

    # Momentum: capped so one exceptional horizon cannot dominate.
    score += max(-8.0, min(8.0, perf_1m * 0.8))
    score += max(-10.0, min(10.0, perf_3m * 0.45))
    score += max(-7.0, min(7.0, perf_6m * 0.18))
    score += max(-7.0, min(7.0, perf_1y * 0.10))

    # Trend quality / reversal.
    score += max(-4.0, min(4.0, macd_hist * 4.0))
    score += 3.0 if reversal else 0.0
    score += max(-3.0, min(3.0, relative_strength * 0.12))

    # Morningstar policy requested for the committee.
    if rating is not None:
        if rating >= 4.0:
            score += 5.0
        elif rating >= 3.0:
            score += 2.5

    # RSI: reward constructive momentum, penalise overbought/oversold extremes.
    if rsi is not None:
        if 50.0 <= rsi <= 68.0:
            score += 4.0
        elif 68.0 < rsi <= 72.0:
            score += 1.0
        elif rsi > 72.0:
            score -= min(8.0, (rsi - 72.0) * 0.9 + 2.0)
        elif rsi < 35.0:
            score -= 5.0

    # Risk/drawdown/volatility penalties. Risk 6-7 corresponds to the high-risk
    # end of the standard 1-7 product-risk scale when available.
    if risk is not None:
        if risk >= 6.0:
            score -= 8.0
        elif risk >= 5.0:
            score -= 4.0
    if drawdown is not None and drawdown < -20.0:
        score -= min(8.0, abs(drawdown + 20.0) * 0.25 + 2.0)
    if volatility is not None and volatility > 30.0:
        score -= min(6.0, (volatility - 30.0) * 0.15 + 1.0)

    score = round(max(0.0, min(100.0, score)), 2)

    overbought = rsi is not None and rsi > 72.0
    high_risk = risk is not None and risk >= 6.0
    deep_drawdown = drawdown is not None and drawdown < -30.0

    if high_risk or deep_drawdown:
        return score, "REVIEW", "RESEARCH_ONLY", "ETF_RISK_GATE"
    if score >= 72.0 and not overbought and perf_3m > 0 and perf_1y > 0:
        return score, "BUY_CANDIDATE", "RECOMMENDATION_ONLY", "ETF_SCORE_TREND_CONFIRMED"
    if score >= 65.0:
        reason = "ETF_OVERBOUGHT_WAIT_RETRACE" if overbought else "ETF_PARTIAL_CONFIRMATION"
        return score, "WATCH", "RECOMMENDATION_ONLY", reason
    return score, "REVIEW", "RESEARCH_ONLY", "ETF_INSUFFICIENT_CONFIRMATION"


def apply_etf_policy(root: Path | None = None) -> dict:
    from v182.io.frames import load_master, save_master

    root = root or ROOT
    outputs = root / "outputs"
    etf_path = outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    decisions_path = outputs / "V20.4_GITOK_ETF_DECISIONS.csv"
    summary_path = outputs / "V20.4_GITOK_COMMITTEE_SUMMARY.md"

    etf = load_master(etf_path).astype(object)
    classified = etf.apply(score_etf, axis=1, result_type="expand")
    classified.columns = ["etf_committee_score", "decision", "execution", "decision_reason"]
    for column in classified.columns:
        etf[column] = classified[column].values
    save_master(etf, etf_path)

    fields = [c for c in [
        "isin", "name", "yahoo_ticker", "provider", "category", "morningstar_rating",
        "risk_indicator", "last_close", "perf_1m_pct", "perf_3m_pct", "perf_6m_pct",
        "perf_1y_pct", "rsi14", "macd", "macd_signal", "macd_hist", "mm20", "mm50",
        "mm100", "mm200", "max_drawdown_1y", "volatility_60d", "relative_strength",
        "positive_reversal_flag", "etf_committee_score", "decision", "execution",
        "decision_reason",
    ] if c in etf.columns]
    decisions = etf[fields].copy().sort_values("etf_committee_score", ascending=False)
    decisions.to_csv(decisions_path, sep=";", index=False, encoding="utf-8-sig")

    counts = decisions["decision"].value_counts(dropna=False).to_dict()
    priority = decisions[decisions["decision"].isin(["BUY_CANDIDATE", "WATCH"])].head(20)
    lines = [
        "",
        "## ETF PEA — décisions V20.4 GitOK",
        "",
        f"- BUY_CANDIDATE: {counts.get('BUY_CANDIDATE', 0)}",
        f"- WATCH: {counts.get('WATCH', 0)}",
        f"- REVIEW: {counts.get('REVIEW', 0)}",
        "",
        "### Priorités ETF PEA",
    ]
    for _, row in priority.iterrows():
        lines.append(
            f"- {row.get('name', '')} ({row.get('yahoo_ticker', '')}) — score {row.get('etf_committee_score', '')}"
            f" — 3m {row.get('perf_3m_pct', '')}% — 1a {row.get('perf_1y_pct', '')}%"
            f" — RSI {row.get('rsi14', '')} — Morningstar {row.get('morningstar_rating', '')}★"
            f" — {row.get('decision', '')}"
        )
    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    return {
        "rows": len(decisions),
        "buy_candidate": int(counts.get("BUY_CANDIDATE", 0)),
        "watch": int(counts.get("WATCH", 0)),
        "review": int(counts.get("REVIEW", 0)),
    }


def main() -> None:
    print("V20_4_GITOK_ETF_POLICY", apply_etf_policy())


if __name__ == "__main__":
    main()
