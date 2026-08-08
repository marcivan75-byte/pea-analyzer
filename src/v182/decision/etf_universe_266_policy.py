from __future__ import annotations

from pathlib import Path
import base64
import gzip
import io

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "data" / "reference" / "ETF_PEA_UNIVERSE_266_COMMITTEE.csv.gz.b64"
DAILY = ROOT / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
OUT = ROOT / "outputs" / "V20.4_GITOK_ETF_266_DECISIONS.csv"
SUMMARY = ROOT / "outputs" / "V20.4_GITOK_COMMITTEE_SUMMARY.md"


def _num(value, default=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(v) else v


def _load_universe(root: Path) -> pd.DataFrame:
    payload = (root / "data" / "reference" / SNAPSHOT.name).read_text(encoding="utf-8").strip()
    raw = gzip.decompress(base64.b64decode(payload)).decode("utf-8-sig")
    df = pd.read_csv(io.StringIO(raw), sep=";", dtype=object)
    if len(df) != 266:
        raise RuntimeError(f"Expected 266 ETF rows, got {len(df)}")
    if df["ISIN"].astype(str).duplicated().any():
        raise RuntimeError("Duplicate ISIN in 266 ETF universe")
    return df


def _daily_overlay(root: Path, universe: pd.DataFrame) -> pd.DataFrame:
    daily_path = root / "outputs" / DAILY.name
    universe = universe.copy()
    universe["daily_match"] = False
    if not daily_path.exists():
        return universe
    daily = pd.read_csv(daily_path, sep=";", dtype=object)
    if "isin" not in daily.columns:
        return universe
    keep = [c for c in [
        "isin", "yahoo_ticker", "last_close", "perf_1m_pct", "perf_3m_pct", "perf_6m_pct",
        "perf_1y_pct", "rsi14", "macd_hist", "max_drawdown_1y", "volatility_60d",
        "relative_strength", "positive_reversal_flag", "morningstar_rating", "risk_indicator"
    ] if c in daily.columns]
    d = daily[keep].copy().drop_duplicates("isin")
    d = d.rename(columns={c: f"daily_{c}" for c in keep if c != "isin"})
    merged = universe.merge(d, left_on="ISIN", right_on="isin", how="left")
    merged["daily_match"] = merged.get("daily_yahoo_ticker", pd.Series(index=merged.index, dtype=object)).notna()
    return merged


def _structural_score(row: pd.Series) -> float:
    # Start from the richer V9 score when available, otherwise V5.
    base = _num(row.get("Score V9"), _num(row.get("SCORE V5 /100"), 50.0))
    score = 0.58 * base
    score += 0.12 * (_num(row.get("Score liquidité combiné /100"), 50.0))
    score += 0.08 * (_num(row.get("Score réplication /100"), 50.0))
    score += 0.05 * (_num(row.get("Score ESG détaillé /100"), 50.0))
    score += 0.05 * min(100.0, _num(row.get("Dispo Score /3"), 0.0) / 3.0 * 100.0)

    sharpe = _num(row.get("Sharpe"))
    sortino = _num(row.get("Sortino"))
    calmar = _num(row.get("Calmar"))
    if sharpe is not None:
        score += max(-3.0, min(3.0, sharpe * 2.0))
    if sortino is not None:
        score += max(-2.0, min(2.0, sortino))
    if calmar is not None:
        score += max(-2.0, min(2.0, calmar))

    aum = _num(row.get("AUM M€"))
    spread = _num(row.get("Spread %"))
    ter = _num(row.get("TER %"))
    srri = _num(row.get("SRRI"))
    te = _num(row.get("Tracking Error %"))
    dd = _num(row.get("Max DD %"))
    adv = _num(row.get("ADV M€"))

    if aum is not None:
        if aum < 15: score -= 12
        elif aum < 50: score -= 5
        elif aum >= 500: score += 3
    if spread is not None:
        if spread > 1.20: score -= 12
        elif spread > 0.70: score -= 6
        elif spread <= 0.20: score += 3
    if ter is not None:
        if ter > 0.70: score -= 5
        elif ter <= 0.20: score += 2
    if srri is not None and srri >= 6: score -= 3
    if te is not None and te > 0.60: score -= 4
    if dd is not None and dd < -40: score -= 4
    if adv is not None and adv >= 10: score += 2

    return round(max(0.0, min(100.0, score)), 2)


def _technical_adjustment(row: pd.Series) -> tuple[float, str]:
    if not bool(row.get("daily_match")):
        return 0.0, "NO_DAILY_MATCH"
    p3 = _num(row.get("daily_perf_3m_pct"), 0.0)
    p1y = _num(row.get("daily_perf_1y_pct"), 0.0)
    rsi = _num(row.get("daily_rsi14"))
    macd = _num(row.get("daily_macd_hist"), 0.0)
    rs = _num(row.get("daily_relative_strength"), 0.0)
    adj = max(-4, min(4, p3 * 0.20)) + max(-3, min(3, p1y * 0.05))
    adj += max(-2, min(2, macd * 2.0)) + max(-2, min(2, rs * 0.08))
    if rsi is not None:
        if 50 <= rsi <= 68: adj += 2
        elif rsi > 72: adj -= min(5, 2 + (rsi - 72) * 0.6)
        elif rsi < 35: adj -= 3
    return round(adj, 2), "DAILY_CONFIRMED"


def _decision(row: pd.Series) -> tuple[str, str, str]:
    score = _num(row.get("etf_266_score"), 0.0)
    aum = _num(row.get("AUM M€"))
    spread = _num(row.get("Spread %"))
    kill = str(row.get("KILL raison") or "").strip()
    daily = bool(row.get("daily_match"))
    p3 = _num(row.get("daily_perf_3m_pct"), 0.0)
    p1y = _num(row.get("daily_perf_1y_pct"), 0.0)
    rsi = _num(row.get("daily_rsi14"))

    hard_fail = (aum is not None and aum < 15) or (spread is not None and spread > 1.50)
    if hard_fail:
        return "REJECT", "RESEARCH_ONLY", kill or "ETF_HARD_LIQUIDITY_GATE"
    if daily and score >= 72 and p3 > 0 and p1y > 0 and (rsi is None or rsi <= 72):
        return "BUY_CANDIDATE", "RECOMMENDATION_ONLY", "STRUCTURE_AND_TIMING_CONFIRMED"
    if daily and score >= 65:
        return "WATCH", "RECOMMENDATION_ONLY", "STRUCTURE_OK_TIMING_PARTIAL"
    if (not daily) and score >= 72:
        return "STRUCTURAL_CANDIDATE", "RESEARCH_ONLY", "STRONG_STRUCTURE_DAILY_DATA_REQUIRED"
    if score >= 60:
        return "REVIEW", "RESEARCH_ONLY", "MIXED_CONFIRMATION"
    return "REJECT", "RESEARCH_ONLY", kill or "INSUFFICIENT_QUALITY"


def apply_266_policy(root: Path | None = None) -> dict:
    root = root or ROOT
    universe = _daily_overlay(root, _load_universe(root))
    universe["structural_score_266"] = universe.apply(_structural_score, axis=1)
    tech = universe.apply(_technical_adjustment, axis=1, result_type="expand")
    tech.columns = ["technical_adjustment", "technical_status"]
    universe[tech.columns] = tech
    universe["etf_266_score"] = (universe["structural_score_266"].astype(float) + universe["technical_adjustment"].astype(float)).clip(0, 100).round(2)
    dec = universe.apply(_decision, axis=1, result_type="expand")
    dec.columns = ["decision", "execution", "decision_reason"]
    universe[dec.columns] = dec
    universe = universe.sort_values(["decision", "etf_266_score"], ascending=[True, False])

    out = root / "outputs" / OUT.name
    out.parent.mkdir(parents=True, exist_ok=True)
    universe.to_csv(out, sep=";", index=False, encoding="utf-8-sig")

    counts = universe["decision"].value_counts().to_dict()
    top_global = universe.sort_values("etf_266_score", ascending=False).head(20)
    lines = ["", "## ETF PEA — Univers maître 266", "",
             f"- Univers analysé: {len(universe)} ETF",
             f"- Daily exact matches: {int(universe['daily_match'].sum())}",
             f"- BUY_CANDIDATE: {counts.get('BUY_CANDIDATE', 0)}",
             f"- WATCH: {counts.get('WATCH', 0)}",
             f"- STRUCTURAL_CANDIDATE: {counts.get('STRUCTURAL_CANDIDATE', 0)}",
             f"- REVIEW: {counts.get('REVIEW', 0)}",
             f"- REJECT: {counts.get('REJECT', 0)}",
             "", "### Top 20 ETF PEA — score global 266"]
    for _, r in top_global.iterrows():
        lines.append(f"- {r.get('Nom complet ETF','')} ({r.get('Ticker','')}) — {r.get('etf_266_score','')} — {r.get('decision','')} — AUM {r.get('AUM M€','')} M€ — TER {r.get('TER %','')}% — spread {r.get('Spread %','')}%")
    with (root / "outputs" / SUMMARY.name).open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return {"rows": len(universe), "daily_matches": int(universe["daily_match"].sum()), **{k.lower(): int(v) for k, v in counts.items()}}


def main() -> None:
    print("V20_4_GITOK_ETF_266_POLICY", apply_266_policy())


if __name__ == "__main__":
    main()
