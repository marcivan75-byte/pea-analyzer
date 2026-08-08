from __future__ import annotations

from pathlib import Path
import os
import re
import unicodedata
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = ROOT / "data" / "external" / "ACTIONS_PEA_3609_LATEST.xlsx"
CANONICAL_DAILY = ROOT / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
OUT = ROOT / "outputs" / "V20.4_GITOK_ACTIONS_3609_DECISIONS.csv"
AUDIT = ROOT / "outputs" / "audit" / "V20.4_ACTIONS_3609_AUDIT.json"


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _norm_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(sa|se|nv|plc|ag|spa|sarl|group|holding|holdings)\b", " ", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _isin_valid(value: object) -> bool:
    isin = str(value or "").strip().upper()
    if len(isin) != 12 or not isin[:2].isalpha() or not isin[2:].isalnum():
        return False
    digits = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in isin)
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
        total += d // 10 + d % 10
    return total % 10 == 0


def _pct_rank(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    s = _num(series)
    pct = s.rank(pct=True, method="average") * 100.0
    if not higher_is_better:
        pct = 100.0 - pct
    return pct.fillna(50.0).clip(0, 100)


def _mean_scores(df: pd.DataFrame, specs: list[tuple[str, bool, float]]) -> pd.Series:
    weighted = []
    weights = []
    for col, higher, weight in specs:
        if col not in df.columns:
            continue
        weighted.append(_pct_rank(df[col], higher) * weight)
        weights.append(weight)
    if not weighted:
        return pd.Series(50.0, index=df.index)
    return sum(weighted) / sum(weights)


def _source_score(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(50.0, index=df.index)
    s = _num(df[column])
    # Pillar scores are 0-3 in the supplied file; V10 is already /100.
    if column.startswith("Score ") and column != "Score V10 /100":
        return (s / 3.0 * 100.0).fillna(50.0).clip(0, 100)
    return s.fillna(50.0).clip(0, 100)


def _load_source(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing daily 3609-actions workbook: {path}. The independent 18:00 updater must publish this path."
        )
    df = pd.read_excel(path, sheet_name=0, dtype=object)
    if len(df) < 3500:
        raise RuntimeError(f"3609 source unexpectedly small: {len(df)} rows")
    required = {"Nom société", "ISIN", "Cours €", "Score V10 /100", "Verdict V10"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"Missing mandatory 3609 source columns: {sorted(missing)}")
    if df["Nom société"].astype(str).str.strip().duplicated().any():
        raise RuntimeError("Duplicate company names in 3609 source")
    return df


def _identity_overlay(root: Path, df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["source_isin_valid"] = out["ISIN"].map(_isin_valid)
    out["identity_method"] = "UNVERIFIED_SOURCE"
    out["identity_confidence"] = 0.35
    out["canonical_isin"] = ""
    out["canonical_name"] = out["Nom société"].astype(str)

    if not (root / "outputs" / CANONICAL_DAILY.name).exists():
        return out

    daily = pd.read_csv(root / "outputs" / CANONICAL_DAILY.name, sep=";", dtype=object, encoding="utf-8-sig")
    if not {"isin", "name"}.issubset(daily.columns):
        return out

    by_isin = {str(r["isin"]).strip().upper(): r for _, r in daily.iterrows() if str(r.get("isin") or "").strip()}
    by_name = {_norm_name(r["name"]): r for _, r in daily.iterrows() if _norm_name(r.get("name"))}
    ticker_cols = [c for c in ["yahoo_ticker", "euronext_symbol"] if c in daily.columns]
    by_ticker: dict[str, pd.Series] = {}
    for _, r in daily.iterrows():
        for c in ticker_cols:
            t = str(r.get(c) or "").strip().upper()
            if t:
                by_ticker[t] = r

    canonical_last = []
    canonical_atr = []
    canonical_rsi = []
    canonical_macd = []
    canonical_mm20 = []
    canonical_mm50 = []
    canonical_mm200 = []
    canonical_perf1m = []
    canonical_perf3m = []
    canonical_perf1y = []

    for i, row in out.iterrows():
        match = None
        isin = str(row.get("ISIN") or "").strip().upper()
        if out.at[i, "source_isin_valid"] and isin in by_isin:
            match = by_isin[isin]
            out.at[i, "identity_method"] = "ISIN_EXACT"
            out.at[i, "identity_confidence"] = 1.00
        if match is None:
            n = _norm_name(row.get("Nom société"))
            if n and n in by_name:
                match = by_name[n]
                out.at[i, "identity_method"] = "NAME_EXACT_CANONICAL"
                out.at[i, "identity_confidence"] = 0.98
        if match is None:
            source_tickers = [str(row.get(c) or "").strip().upper() for c in ["Ticker Bloomberg", "Ticker Reuters", "Code"] if c in out.columns]
            for t in source_tickers:
                if t and t in by_ticker:
                    match = by_ticker[t]
                    out.at[i, "identity_method"] = "TICKER_EXACT"
                    out.at[i, "identity_confidence"] = 0.92
                    break
        if match is not None:
            out.at[i, "canonical_isin"] = str(match.get("isin") or "")
            out.at[i, "canonical_name"] = str(match.get("name") or row.get("Nom société") or "")
            canonical_last.append(match.get("last_close"))
            canonical_atr.append(match.get("atr14"))
            canonical_rsi.append(match.get("rsi14"))
            canonical_macd.append(match.get("macd_hist"))
            canonical_mm20.append(match.get("mm20"))
            canonical_mm50.append(match.get("mm50"))
            canonical_mm200.append(match.get("mm200"))
            canonical_perf1m.append(match.get("perf_1m_pct"))
            canonical_perf3m.append(match.get("perf_3m_pct"))
            canonical_perf1y.append(match.get("perf_1y_pct"))
        else:
            canonical_last.append(None); canonical_atr.append(None); canonical_rsi.append(None); canonical_macd.append(None)
            canonical_mm20.append(None); canonical_mm50.append(None); canonical_mm200.append(None)
            canonical_perf1m.append(None); canonical_perf3m.append(None); canonical_perf1y.append(None)

    out["canonical_last_close"] = canonical_last
    out["canonical_atr14"] = canonical_atr
    out["canonical_rsi14"] = canonical_rsi
    out["canonical_macd_hist"] = canonical_macd
    out["canonical_mm20"] = canonical_mm20
    out["canonical_mm50"] = canonical_mm50
    out["canonical_mm200"] = canonical_mm200
    out["canonical_perf_1m_pct"] = canonical_perf1m
    out["canonical_perf_3m_pct"] = canonical_perf3m
    out["canonical_perf_1y_pct"] = canonical_perf1y
    return out


def _calculate_scores(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    quality = _mean_scores(out, [
        ("ROE %", True, 1), ("ROIC %", True, 1.2), ("ROCE %", True, 1),
        ("FCF Yield %", True, 1), ("FCF CAGR 3a %", True, 0.8), ("CA CAGR 3a %", True, 0.7),
        ("Marge opérationnelle %", True, 0.8), ("Score Piotroski 0-9", True, 0.9),
        ("Score Altman Z", True, 0.8), ("Score Beneish M", False, 0.6), ("Dette nette / EBITDA", False, 0.8),
    ])
    value = _mean_scores(out, [
        ("PER 2025 est", False, 1), ("PEG Ratio", False, 0.8), ("P/B", False, 0.5),
        ("EV/EBITDA", False, 1), ("EV/FCF", False, 1), ("FCF Yield %", True, 1),
        ("Rendement dividende %", True, 0.5), ("Upside %", True, 0.8),
    ])
    momentum = _mean_scores(out, [
        ("Momentum 1m %", True, 1.2), ("Momentum 3m %", True, 1.4), ("Momentum 6m %", True, 1.2),
        ("Momentum 12m %", True, 0.8), ("Distance MM200 %", True, 1), ("Distance 52W High %", True, 0.6),
    ])
    risk = _mean_scores(out, [
        ("Beta 5a vs Stoxx600", False, 0.6), ("Vol 5a %", False, 1), ("Max DD 5a %", True, 1),
        ("Ulcer Index", False, 1), ("VaR graduel", False, 0.8), ("Dette nette / EBITDA", False, 0.8),
        ("Jours pour couvrir", False, 0.5), ("Short interest %", False, 0.5), ("Correl portefeuille", False, 0.5),
    ])
    catalyst = _mean_scores(out, [
        ("Upside %", True, 1.3), ("Nb analystes", True, 0.5), ("Surprise EPS %", True, 0.8),
        ("Rachat actions %", True, 0.5), ("% institutionnels", True, 0.4), ("% insiders", True, 0.3),
        ("Score gouvernance", True, 0.5), ("Controverses ESG", False, 0.4),
    ])
    structure = _mean_scores(out, [
        ("Capitalisation M€", True, 0.7), ("ADV €", True, 1), ("Volume moyen 20j €", True, 0.7),
        ("Turnover %", True, 0.5), ("Dispo Score /3", True, 0.6),
    ])
    expectancy = _mean_scores(out, [
        ("Expectancy %", True, 1), ("Payoff", True, 0.8), ("WinRate %", True, 0.8),
        ("Sortino", True, 0.8), ("Calmar", True, 0.8), ("Kelly/2 %", True, 0.5), ("PF", True, 0.7),
    ])

    # Blend raw criteria with the supplied V10 pillar scores. This makes every major family auditable
    # while avoiding double counting any single metric excessively.
    out["score_quality_100"] = (0.65 * quality + 0.35 * _source_score(out, "Score QUALITY")).round(2)
    out["score_value_100"] = value.round(2)
    out["score_momentum_100"] = (0.70 * momentum + 0.30 * _source_score(out, "Score MOMENTUM")).round(2)
    out["score_risk_100"] = (0.70 * risk + 0.30 * _source_score(out, "Score RISQUE")).round(2)
    out["score_catalyst_100"] = catalyst.round(2)
    out["score_structure_100"] = (0.65 * structure + 0.35 * _source_score(out, "Score STRUCTURE")).round(2)
    out["score_expectancy_100"] = (0.65 * expectancy + 0.35 * _source_score(out, "Score EXPECTANCY")).round(2)
    out["score_sector_100"] = _source_score(out, "Score SECTEUR").round(2)
    out["score_fiscal_100"] = _source_score(out, "Score FISCALITE").round(2)
    out["score_rotation_100"] = _source_score(out, "Score ROTATION").round(2)

    out["score_short_term"] = (
        0.38 * out["score_momentum_100"] + 0.18 * out["score_catalyst_100"] +
        0.15 * out["score_risk_100"] + 0.12 * out["score_expectancy_100"] +
        0.10 * out["score_structure_100"] + 0.07 * out["score_sector_100"]
    ).round(2)
    out["score_medium_term"] = (
        0.25 * out["score_momentum_100"] + 0.20 * out["score_catalyst_100"] +
        0.20 * out["score_quality_100"] + 0.15 * out["score_value_100"] +
        0.10 * out["score_risk_100"] + 0.10 * out["score_expectancy_100"]
    ).round(2)
    out["score_long_term"] = (
        0.30 * out["score_quality_100"] + 0.20 * out["score_value_100"] +
        0.15 * out["score_structure_100"] + 0.12 * out["score_risk_100"] +
        0.10 * out["score_fiscal_100"] + 0.08 * out["score_expectancy_100"] +
        0.05 * out["score_sector_100"]
    ).round(2)

    v10 = _source_score(out, "Score V10 /100")
    out["committee_score_3609"] = (
        0.25 * v10 + 0.25 * out["score_short_term"] +
        0.25 * out["score_medium_term"] + 0.25 * out["score_long_term"]
    ).round(2)

    bearish_momentum = 100.0 - out["score_momentum_100"]
    weak_fundamentals = 100.0 - (0.55 * out["score_quality_100"] + 0.45 * out["score_value_100"])
    positioning = _mean_scores(out, [("Short interest %", True, 1), ("Jours pour couvrir", True, 0.7)])
    out["short_thesis_score"] = (
        0.35 * bearish_momentum + 0.30 * weak_fundamentals +
        0.20 * (100.0 - out["score_risk_100"]) + 0.15 * positioning
    ).round(2).clip(0, 100)

    return out


def _horizon_and_timing(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    conf = _num(out["identity_confidence"]).fillna(0)
    score = _num(out["committee_score_3609"]).fillna(0)
    short_score = _num(out["score_short_term"]).fillna(0)
    medium_score = _num(out["score_medium_term"]).fillna(0)
    long_score = _num(out["score_long_term"]).fillna(0)
    short_thesis = _num(out["short_thesis_score"]).fillna(0)

    out["T0"] = np.select([short_score >= 78, short_score >= 70], ["ACTIONABLE_SETUP", "WATCH"], default="WAIT")
    out["T1_1_4w"] = np.select([short_score >= 78, short_score >= 70], ["BUY_SETUP", "WATCH"], default="WAIT")
    out["T2_1_3m"] = np.select([medium_score >= 76, medium_score >= 68], ["ACCUMULATE", "WATCH"], default="WAIT")
    out["T3_3_6m"] = np.select([medium_score >= 78, medium_score >= 70], ["ACCUMULATE", "WATCH"], default="WAIT")
    out["T4_6_12m"] = np.select([long_score >= 76, long_score >= 68], ["CORE_CANDIDATE", "WATCH"], default="WAIT")
    out["T5_12_24m"] = np.select([long_score >= 78, long_score >= 70], ["CORE_CANDIDATE", "WATCH"], default="WAIT")
    out["short_signal"] = np.select([short_thesis >= 78, short_thesis >= 68], ["SHORT_THESIS_STRONG", "SHORT_RISK_WATCH"], default="NO_SHORT_THESIS")

    price = _num(out.get("canonical_last_close", pd.Series(index=out.index, dtype=float)))
    source_price = _num(out.get("Cours €", pd.Series(index=out.index, dtype=float)))
    price = price.where(price.notna(), source_price.where(conf >= 0.92))
    atr = _num(out.get("canonical_atr14", pd.Series(index=out.index, dtype=float)))
    rsi = _num(out.get("canonical_rsi14", pd.Series(index=out.index, dtype=float)))
    rsi = rsi.where(rsi.notna(), _num(out.get("RSI 14j", pd.Series(index=out.index, dtype=float))))

    reliable = (conf >= 0.92) & price.notna()
    low = pd.Series(np.nan, index=out.index)
    high = pd.Series(np.nan, index=out.index)
    target = pd.Series(np.nan, index=out.index)
    invalid = pd.Series(np.nan, index=out.index)

    with_atr = reliable & atr.notna() & (atr > 0)
    low.loc[with_atr] = price[with_atr] - 0.50 * atr[with_atr]
    high.loc[with_atr] = price[with_atr] + 0.10 * atr[with_atr]
    target.loc[with_atr] = price[with_atr] + 1.50 * atr[with_atr]
    invalid.loc[with_atr] = price[with_atr] - 1.00 * atr[with_atr]

    no_atr = reliable & ~with_atr
    overbought = no_atr & (rsi > 72)
    low.loc[overbought] = price[overbought] * 0.95
    high.loc[overbought] = price[overbought] * 0.97
    normal = no_atr & ~overbought
    low.loc[normal] = price[normal] * 0.98
    high.loc[normal] = price[normal]

    out["T1_entry_low"] = low.round(4)
    out["T1_entry_high"] = high.round(4)
    out["T1_target"] = target.round(4)
    out["T1_invalidation"] = invalid.round(4)
    out["timing_comment"] = np.where(
        ~reliable,
        "IDENTITY_OR_DAILY_DATA_REQUIRED",
        np.where(rsi > 72, "WAIT_3_5PCT_PULLBACK", np.where(short_score >= 78, "FRACTIONAL_ENTRY_ON_CONFIRMATION", "WAIT_FOR_TRIGGER")),
    )

    actionable = conf >= 0.92
    out["decision"] = np.select(
        [
            short_thesis >= 78,
            actionable & (score >= 77) & (short_score >= 74) & (medium_score >= 72),
            actionable & (score >= 72),
            (~actionable) & (score >= 77),
            score >= 64,
        ],
        ["SHORT_THESIS", "BUY_CANDIDATE", "WATCH", "STRUCTURAL_CANDIDATE", "REVIEW"],
        default="AVOID",
    )
    out["execution"] = np.where(out["decision"].isin(["BUY_CANDIDATE", "WATCH"]), "RECOMMENDATION_ONLY", "RESEARCH_ONLY")
    out["decision_reason"] = np.select(
        [out["decision"].eq("SHORT_THESIS"), out["decision"].eq("BUY_CANDIDATE"), out["decision"].eq("STRUCTURAL_CANDIDATE")],
        ["BEARISH_MULTI_HORIZON_THESIS", "IDENTITY_AND_MULTI_HORIZON_CONFIRMED", "HIGH_SCORE_IDENTITY_OR_DAILY_DATA_REQUIRED"],
        default="MULTI_HORIZON_POLICY",
    )
    return out


def apply_3609_policy(root: Path | None = None, source_path: Path | None = None) -> dict:
    root = root or ROOT
    source = source_path or Path(os.getenv("V204_ACTIONS_3609_SOURCE", str(DEFAULT_SOURCE)))
    df = _load_source(source)
    original_columns = list(df.columns)
    df = _identity_overlay(root, df)
    df = _calculate_scores(df)
    df = _horizon_and_timing(df)

    # Preserve every source criterion, then append all derived fields.
    derived = [c for c in df.columns if c not in original_columns]
    df = df[original_columns + derived]
    out = root / "outputs" / OUT.name
    out.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values("committee_score_3609", ascending=False).to_csv(out, sep=";", index=False, encoding="utf-8-sig")

    import json
    audit = {
        "rows": int(len(df)),
        "source_columns": int(len(original_columns)),
        "source_isin_valid": int(df["source_isin_valid"].sum()),
        "identity_methods": {str(k): int(v) for k, v in df["identity_method"].value_counts().to_dict().items()},
        "decision_counts": {str(k): int(v) for k, v in df["decision"].value_counts().to_dict().items()},
        "execution_live_enabled": False,
        "source_path": str(source),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    audit_path = root / "outputs" / "audit" / AUDIT.name
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> None:
    print("V20_4_GITOK_ACTIONS_3609_POLICY", apply_3609_policy())


if __name__ == "__main__":
    main()
