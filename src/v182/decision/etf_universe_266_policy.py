from __future__ import annotations

from pathlib import Path
import base64
from difflib import SequenceMatcher
import gzip
import io
import re
import unicodedata

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
PAYLOAD_PARTS = (
    "ETF_PEA_UNIVERSE_266_COMPACT.b64.part1",
    "ETF_PEA_UNIVERSE_266_COMPACT.b64.part2",
)
DAILY = ROOT / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
OUT = ROOT / "outputs" / "V20.4_GITOK_ETF_266_DECISIONS.csv"
SUMMARY = ROOT / "outputs" / "V20.4_GITOK_COMMITTEE_SUMMARY.md"

ISSUER_TOKENS = {
    "amundi", "lyxor", "bnpp", "bnp", "paribas", "hsbc", "invesco",
    "xtrackers", "ishares", "vanguard", "ossiam", "wisdomtree",
}


def _num(value, default=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(v) else v


def _norm_text(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode().lower()
    text = re.sub(
        r"\b(ucits|etf|pea|acc|dist|eur|hedged|hdg|capitalisation|distribution)\b",
        " ",
        text,
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _ticker_key(value) -> str:
    value = str(value or "").strip().upper()
    return value.split(".")[0] if value else ""


def _issuer_tokens(value) -> set[str]:
    return set(_norm_text(value).split()) & ISSUER_TOKENS


def _load_universe(root: Path) -> pd.DataFrame:
    base = root / "data" / "reference"
    chunks = []
    for name in PAYLOAD_PARTS:
        path = base / name
        if not path.exists():
            raise RuntimeError(f"Missing 266 ETF payload part: {path}")
        chunks.append(path.read_text(encoding="utf-8").strip())
    payload = "".join(chunks)
    raw = gzip.decompress(base64.b64decode(payload, validate=True)).decode("utf-8-sig")
    df = pd.read_csv(io.StringIO(raw), sep=";", dtype=object)
    if len(df) != 266:
        raise RuntimeError(f"Expected 266 ETF rows, got {len(df)}")
    if "ISIN" not in df.columns:
        raise RuntimeError("Missing ISIN column in 266 ETF universe")
    isin = df["ISIN"].astype(str).str.strip()
    if isin.eq("").any() or isin.duplicated().any():
        raise RuntimeError("Missing or duplicate ISIN in 266 ETF universe")
    return df


def _daily_payload_columns(daily: pd.DataFrame) -> list[str]:
    return [
        c
        for c in [
            "isin", "name", "official_benchmark", "ticker_euronext", "ticker_primary",
            "euronext_symbol", "ticker_yahoo_final", "ticker_yahoo", "yahoo_ticker",
            "last_close", "perf_1m_pct", "perf_3m_pct", "perf_6m_pct", "perf_1y_pct",
            "rsi14", "macd_hist", "max_drawdown_1y", "volatility_60d",
            "relative_strength", "positive_reversal_flag", "morningstar_rating",
            "risk_indicator",
        ]
        if c in daily.columns
    ]


def _attach_daily_row(universe: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    result = universe.copy()
    result["daily_match"] = False
    result["daily_match_method"] = "NONE"
    result["daily_match_confidence"] = 0.0
    result["daily_source_row"] = pd.NA

    if daily.empty:
        return result

    daily = daily.reset_index(drop=True).copy()
    daily["_isin_key"] = daily.get("isin", pd.Series(index=daily.index, dtype=object)).astype(str).str.strip()
    result["_isin_key"] = result["ISIN"].astype(str).str.strip()

    # 1. Exact ISIN identity.
    isin_map: dict[str, list[int]] = {}
    for j, key in daily["_isin_key"].items():
        if key and key.lower() != "nan":
            isin_map.setdefault(key, []).append(j)
    for i, key in result["_isin_key"].items():
        candidates = isin_map.get(key, [])
        if len(candidates) == 1:
            result.at[i, "daily_source_row"] = candidates[0]
            result.at[i, "daily_match_method"] = "ISIN_EXACT"
            result.at[i, "daily_match_confidence"] = 1.0

    # 2. Exact ticker identity, only when the ticker maps to one daily row.
    ticker_cols = [
        c for c in [
            "ticker_euronext", "ticker_primary", "euronext_symbol",
            "ticker_yahoo_final", "ticker_yahoo", "yahoo_ticker",
        ] if c in daily.columns
    ]
    ticker_map: dict[str, set[int]] = {}
    for j, row in daily.iterrows():
        for col in ticker_cols:
            key = _ticker_key(row.get(col))
            if key:
                ticker_map.setdefault(key, set()).add(j)
    for i, row in result[result["daily_source_row"].isna()].iterrows():
        key = _ticker_key(row.get("Ticker"))
        candidates = ticker_map.get(key, set())
        if key and len(candidates) == 1:
            result.at[i, "daily_source_row"] = next(iter(candidates))
            result.at[i, "daily_match_method"] = "TICKER_EXACT"
            result.at[i, "daily_match_confidence"] = 0.98

    # 3. High-similarity name/family proxy. It deliberately cannot create BUY by itself.
    if "name" in daily.columns:
        daily_names = [_norm_text(v) for v in daily["name"]]
        daily_issuers = [_issuer_tokens(v) for v in daily["name"]]
        for i, row in result[result["daily_source_row"].isna()].iterrows():
            source_name = _norm_text(row.get("Nom complet ETF"))
            source_issuer = _issuer_tokens(row.get("Nom complet ETF"))
            if not source_name:
                continue
            scored: list[tuple[float, int]] = []
            for j, candidate_name in enumerate(daily_names):
                candidate_issuer = daily_issuers[j]
                if source_issuer and candidate_issuer and not (source_issuer & candidate_issuer):
                    continue
                ratio = SequenceMatcher(None, source_name, candidate_name).ratio()
                scored.append((ratio, j))
            if not scored:
                continue
            scored.sort(reverse=True)
            best_score, best_j = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else 0.0
            if best_score >= 0.80 and best_score - second_score >= 0.04:
                result.at[i, "daily_source_row"] = best_j
                result.at[i, "daily_match_method"] = "NAME_FAMILY_PROXY"
                result.at[i, "daily_match_confidence"] = round(min(0.85, best_score), 3)

    payload_cols = _daily_payload_columns(daily)
    for col in payload_cols:
        if col == "isin":
            continue
        result[f"daily_{col}"] = pd.NA

    for i, source in result["daily_source_row"].items():
        if pd.isna(source):
            continue
        j = int(source)
        for col in payload_cols:
            if col == "isin":
                continue
            result.at[i, f"daily_{col}"] = daily.at[j, col]

    result["daily_match"] = result["daily_source_row"].notna()
    return result.drop(columns=["_isin_key"])


def _daily_overlay(root: Path, universe: pd.DataFrame) -> pd.DataFrame:
    daily_path = root / "outputs" / DAILY.name
    if not daily_path.exists():
        universe = universe.copy()
        universe["daily_match"] = False
        universe["daily_match_method"] = "NONE"
        universe["daily_match_confidence"] = 0.0
        return universe
    daily = pd.read_csv(daily_path, sep=";", dtype=object)
    return _attach_daily_row(universe, daily)


def _structural_score(row: pd.Series) -> float:
    base = _num(row.get("Score V9"), _num(row.get("SCORE V5 /100"), 50.0))
    score = 0.58 * base
    score += 0.12 * (_num(row.get("Score liquidité combiné /100"), 50.0))
    score += 0.08 * (_num(row.get("Score réplication /100"), 50.0))
    score += 0.05 * (_num(row.get("Score ESG détaillé /100"), 50.0))
    score += 0.05 * min(100.0, _num(row.get("Dispo Score /3"), 0.0) / 3.0 * 100.0)

    sharpe = _num(row.get("Sharpe"))
    if sharpe is not None:
        score += max(-3.0, min(3.0, sharpe * 2.0))

    aum = _num(row.get("AUM M€"))
    spread = _num(row.get("Spread %"))
    ter = _num(row.get("TER %"))
    srri = _num(row.get("SRRI"))
    te = _num(row.get("Tracking Error %"))
    dd = _num(row.get("Max DD %"))
    adv = _num(row.get("ADV M€"))

    if aum is not None:
        if aum < 15:
            score -= 12
        elif aum < 50:
            score -= 5
        elif aum >= 500:
            score += 3
    if spread is not None:
        if spread > 1.20:
            score -= 12
        elif spread > 0.70:
            score -= 6
        elif spread <= 0.20:
            score += 3
    if ter is not None:
        if ter > 0.70:
            score -= 5
        elif ter <= 0.20:
            score += 2
    if srri is not None and srri >= 6:
        score -= 3
    if te is not None and te > 0.60:
        score -= 4
    if dd is not None and dd < -40:
        score -= 4
    if adv is not None and adv >= 10:
        score += 2

    return round(max(0.0, min(100.0, score)), 2)


def _technical_adjustment(row: pd.Series) -> tuple[float, str]:
    if not bool(row.get("daily_match")):
        return 0.0, "NO_DAILY_MATCH"
    confidence = _num(row.get("daily_match_confidence"), 0.0)
    p3 = _num(row.get("daily_perf_3m_pct"), 0.0)
    p1y = _num(row.get("daily_perf_1y_pct"), 0.0)
    rsi = _num(row.get("daily_rsi14"))
    macd = _num(row.get("daily_macd_hist"), 0.0)
    rs = _num(row.get("daily_relative_strength"), 0.0)
    adj = max(-4, min(4, p3 * 0.20)) + max(-3, min(3, p1y * 0.05))
    adj += max(-2, min(2, macd * 2.0)) + max(-2, min(2, rs * 0.08))
    if rsi is not None:
        if 50 <= rsi <= 68:
            adj += 2
        elif rsi > 72:
            adj -= min(5, 2 + (rsi - 72) * 0.6)
        elif rsi < 35:
            adj -= 3
    status = "DAILY_IDENTITY_CONFIRMED" if confidence >= 0.95 else "DAILY_FAMILY_PROXY"
    return round(adj * confidence, 2), status


def _decision(row: pd.Series) -> tuple[str, str, str]:
    score = _num(row.get("etf_266_score"), 0.0)
    structural = _num(row.get("structural_score_266"), 0.0)
    aum = _num(row.get("AUM M€"))
    spread = _num(row.get("Spread %"))
    kill = str(row.get("KILL raison") or "").strip()
    daily = bool(row.get("daily_match"))
    confidence = _num(row.get("daily_match_confidence"), 0.0)
    p3 = _num(row.get("daily_perf_3m_pct"), 0.0)
    p1y = _num(row.get("daily_perf_1y_pct"), 0.0)
    rsi = _num(row.get("daily_rsi14"))

    hard_fail = (aum is not None and aum < 15) or (spread is not None and spread > 1.50)
    if hard_fail:
        return "REJECT", "RESEARCH_ONLY", kill or "ETF_HARD_LIQUIDITY_GATE"

    # BUY requires an identity-grade market match, not a family proxy.
    if daily and confidence >= 0.95 and score >= 72 and p3 > 0 and p1y > 0 and (rsi is None or rsi <= 72):
        return "BUY_CANDIDATE", "RECOMMENDATION_ONLY", "STRUCTURE_AND_TIMING_IDENTITY_CONFIRMED"
    if daily and score >= 65:
        reason = "STRUCTURE_OK_TIMING_PARTIAL" if confidence >= 0.95 else "STRUCTURE_OK_FAMILY_PROXY_TIMING"
        return "WATCH", "RECOMMENDATION_ONLY", reason
    if daily:
        return "REVIEW", "RESEARCH_ONLY", "DAILY_SIGNAL_AVAILABLE_BUT_MIXED"

    # Missing daily data is no longer equivalent to a rejection.
    if structural >= 72:
        return "STRUCTURAL_CANDIDATE", "RESEARCH_ONLY", "STRONG_STRUCTURE_DAILY_DATA_REQUIRED"
    if structural >= 55:
        return "DATA_REQUIRED", "RESEARCH_ONLY", "STRUCTURE_NOT_REJECTED_DAILY_DATA_REQUIRED"
    return "REJECT", "RESEARCH_ONLY", kill or "STRUCTURAL_QUALITY_INSUFFICIENT"


def apply_266_policy(root: Path | None = None) -> dict:
    root = root or ROOT
    universe = _daily_overlay(root, _load_universe(root))
    universe["structural_score_266"] = universe.apply(_structural_score, axis=1)
    tech = universe.apply(_technical_adjustment, axis=1, result_type="expand")
    tech.columns = ["technical_adjustment", "technical_status"]
    universe[tech.columns] = tech
    universe["etf_266_score"] = (
        universe["structural_score_266"].astype(float) + universe["technical_adjustment"].astype(float)
    ).clip(0, 100).round(2)
    dec = universe.apply(_decision, axis=1, result_type="expand")
    dec.columns = ["decision", "execution", "decision_reason"]
    universe[dec.columns] = dec
    universe = universe.sort_values("etf_266_score", ascending=False)

    out = root / "outputs" / OUT.name
    out.parent.mkdir(parents=True, exist_ok=True)
    universe.to_csv(out, sep=";", index=False, encoding="utf-8-sig")

    counts = universe["decision"].value_counts().to_dict()
    methods = universe["daily_match_method"].value_counts().to_dict()
    top_global = universe.head(20)
    lines = [
        "",
        "## ETF PEA — Univers maître 266",
        "",
        f"- Univers analysé: {len(universe)} ETF",
        f"- Daily matches total: {int(universe['daily_match'].sum())}",
        f"- ISIN exact: {methods.get('ISIN_EXACT', 0)}",
        f"- Ticker exact: {methods.get('TICKER_EXACT', 0)}",
        f"- Family proxies: {methods.get('NAME_FAMILY_PROXY', 0)}",
        f"- BUY_CANDIDATE: {counts.get('BUY_CANDIDATE', 0)}",
        f"- WATCH: {counts.get('WATCH', 0)}",
        f"- STRUCTURAL_CANDIDATE: {counts.get('STRUCTURAL_CANDIDATE', 0)}",
        f"- DATA_REQUIRED: {counts.get('DATA_REQUIRED', 0)}",
        f"- REVIEW: {counts.get('REVIEW', 0)}",
        f"- REJECT: {counts.get('REJECT', 0)}",
        "",
        "### Top 20 ETF PEA — score global 266",
    ]
    for _, r in top_global.iterrows():
        lines.append(
            f"- {r.get('Nom complet ETF','')} ({r.get('Ticker','')}) — {r.get('etf_266_score','')}"
            f" — {r.get('decision','')} — match {r.get('daily_match_method','NONE')}"
            f" — AUM {r.get('AUM M€','')} M€ — TER {r.get('TER %','')}% — spread {r.get('Spread %','')}%"
        )
    with (root / "outputs" / SUMMARY.name).open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return {
        "rows": len(universe),
        "daily_matches": int(universe["daily_match"].sum()),
        "identity_matches": int((universe["daily_match_confidence"].astype(float) >= 0.95).sum()),
        "family_proxies": int((universe["daily_match_method"] == "NAME_FAMILY_PROXY").sum()),
        **{k.lower(): int(v) for k, v in counts.items()},
    }


def main() -> None:
    print("V20_4_GITOK_ETF_266_POLICY", apply_266_policy())


if __name__ == "__main__":
    main()
