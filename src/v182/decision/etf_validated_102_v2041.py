from __future__ import annotations

from pathlib import Path
import math
import re

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
IN = ROOT / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
OUT = ROOT / "outputs" / "V20.4.1_ETF_PEA_VALIDATED_102_COMMITTEE.csv"
AUDIT = ROOT / "outputs" / "audit" / "V20.4.1_ETF_PEA_VALIDATED_102_AUDIT.json"


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _pct(s: pd.Series, higher: bool = True) -> pd.Series:
    r = s.rank(pct=True, method="average") * 100.0
    if not higher:
        r = 100.0 - r
    return r.fillna(50.0)


def _rsi_zone(s: pd.Series) -> pd.Series:
    return (100.0 - (s - 60.0).abs() * 3.0).clip(0.0, 100.0).fillna(50.0)


def _isin_valid(value: object) -> bool:
    s = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", s):
        return False
    expanded = "".join(str(ord(c) - 55) if c.isalpha() else c for c in s)
    total = 0
    for i, ch in enumerate(expanded[::-1]):
        x = int(ch) * (2 if i % 2 else 1)
        total += x // 10 + x % 10
    return total % 10 == 0


def build(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if len(df) != 102:
        raise RuntimeError(f"Expected validated ETF reference of 102 rows, got {len(df)}")
    if df["isin"].astype(str).nunique() != 102:
        raise RuntimeError("ETF reference contains duplicate ISIN")
    valid = df["isin"].map(_isin_valid)
    if not valid.all():
        bad = df.loc[~valid, "isin"].astype(str).tolist()[:10]
        raise RuntimeError(f"Invalid ETF ISIN checksum(s): {bad}")
    if "ticker_identity_status" not in df or not df["ticker_identity_status"].eq("FINAL_VALIDATED").all():
        raise RuntimeError("ETF ticker identities are not all FINAL_VALIDATED")
    ticker_conf = _num(df, "ticker_confidence_pct")
    if ticker_conf.lt(95).any():
        raise RuntimeError("ETF ticker confidence below 95%")
    if _num(df, "last_close").isna().any():
        raise RuntimeError("Validated ETF reference must have a last_close for every row")

    mom_ct = (
        .18 * _pct(_num(df, "perf_1m_pct"))
        + .28 * _pct(_num(df, "perf_3m_pct"))
        + .20 * _pct(_num(df, "perf_6m_pct"))
        + .12 * _pct(_num(df, "relative_strength"))
        + .10 * _pct(_num(df, "macd_hist"))
        + .07 * _rsi_zone(_num(df, "rsi14"))
        + .05 * _pct(_num(df, "rvol20"))
    )
    mom_mt = (
        .10 * _pct(_num(df, "perf_1m_pct"))
        + .25 * _pct(_num(df, "perf_3m_pct"))
        + .25 * _pct(_num(df, "perf_6m_pct"))
        + .15 * _pct(_num(df, "perf_1y_pct"))
        + .10 * _pct(_num(df, "relative_strength"))
        + .08 * _pct(_num(df, "macd_hist"))
        + .07 * _rsi_zone(_num(df, "rsi14"))
    )
    mom_lt = (
        .10 * _pct(_num(df, "perf_6m_pct"))
        + .30 * _pct(_num(df, "perf_1y_pct"))
        + .25 * _pct(_num(df, "perf_3y_pct"))
        + .20 * _pct(_num(df, "perf_5y_pct"))
        + .15 * _pct(_num(df, "relative_strength"))
    )

    risk = (
        .25 * _pct(_num(df, "volatility_20d"), False)
        + .25 * _pct(_num(df, "volatility_60d"), False)
        + .30 * _pct(_num(df, "max_drawdown_1y"))
        + .10 * _pct(_num(df, "atr14"), False)
    )
    ri = _num(df, "risk_indicator")
    if ri.notna().any():
        risk = .90 * risk + .10 * _pct(ri, False)

    assets = _num(df, "fund_total_assets_eur_m").fillna(_num(df, "aum_m"))
    liquidity = .45 * _pct(assets) + .30 * _pct(_num(df, "volume")) + .25 * _pct(_num(df, "holdings"))
    tracking = (
        .50 * _pct(_num(df, "tracking_error_1y_pct"), False)
        + .30 * _pct(_num(df, "tracking_error_3y_pct"), False)
        + .20 * _pct(_num(df, "tracking_error_5y_pct"), False)
    )
    costs = _pct(_num(df, "ter_pct"), False)
    diversification = .55 * _pct(_num(df, "holdings")) + .45 * _pct(assets)

    stars = _num(df, "morningstar_rating")
    morningstar = pd.Series(50.0, index=df.index)
    morningstar.loc[stars.eq(3)] = 60.0
    morningstar.loc[stars.eq(4)] = 75.0
    morningstar.loc[stars.eq(5)] = 90.0

    ct = .40 * mom_ct + .25 * risk + .15 * liquidity + .10 * tracking + .05 * costs + .05 * morningstar
    mt = .27 * mom_mt + .25 * risk + .13 * liquidity + .15 * tracking + .10 * costs + .05 * morningstar + .05 * diversification
    lt = .12 * mom_lt + .25 * risk + .10 * liquidity + .20 * tracking + .15 * costs + .08 * morningstar + .10 * diversification
    short = .42 * (100 - mom_ct) + .23 * (100 - risk) + .12 * (100 - tracking) + .08 * (100 - liquidity) + .08 * (100 - costs) + .07 * (100 - morningstar)

    coverage = pd.concat([
        _num(df, "last_close"), _num(df, "perf_3m_pct"), _num(df, "perf_6m_pct"),
        _num(df, "rsi14"), _num(df, "volatility_60d"), _num(df, "max_drawdown_1y"),
    ], axis=1).notna().mean(axis=1)
    confidence = (.75 * (ticker_conf.clip(0, 100) / 100.0) + .25 * coverage).clip(.75, 1.0)

    raw = (.30 * ct + .35 * mt + .35 * lt) * confidence
    committee_pct = raw.rank(pct=True, method="average") * 100.0

    decisions = []
    for i in df.index:
        p3 = _num(df, "perf_3m_pct").loc[i]
        p1y = _num(df, "perf_1y_pct").loc[i]
        rsi = _num(df, "rsi14").loc[i]
        if confidence.loc[i] < .95:
            dec = "DATA_REQUIRED"
        elif committee_pct.loc[i] >= 85 and (pd.isna(p3) or p3 > 0) and (pd.isna(p1y) or p1y > 0) and (pd.isna(rsi) or rsi <= 75):
            dec = "BUY_CANDIDATE"
        elif committee_pct.loc[i] >= 70:
            dec = "WATCH"
        elif committee_pct.loc[i] >= 50:
            dec = "REVIEW"
        else:
            dec = "REJECT"
        decisions.append(dec)

    df["score_short_term"] = ct.round(2)
    df["score_medium_term"] = mt.round(2)
    df["score_long_term"] = lt.round(2)
    df["short_thesis_score"] = short.round(2)
    df["committee_score_raw_v2041_etf"] = raw.round(2)
    df["committee_score_v2041_etf"] = committee_pct.round(2)
    df["identity_confidence_final"] = confidence.round(3)
    df["identity_method_final"] = "ISIN_CHECKSUM+FINAL_VALIDATED_TICKER+DAILY_OHLCV"
    df["decision"] = decisions
    df["execution"] = "RESEARCH_ONLY"
    return df


def main() -> None:
    if not IN.exists():
        raise RuntimeError(f"Missing validated ETF input: {IN}")
    df = pd.read_csv(IN, sep=";", dtype=object, encoding="utf-8-sig")
    out = build(df)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, sep=";", index=False, encoding="utf-8-sig")
    import json
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    audit = {
        "passed": True,
        "rows": int(len(out)),
        "unique_isin": int(out["isin"].nunique()),
        "isin_checksum_valid": int(out["isin"].map(_isin_valid).sum()),
        "final_validated_tickers": int(out["ticker_identity_status"].eq("FINAL_VALIDATED").sum()),
        "last_close_available": int(pd.to_numeric(out["last_close"], errors="coerce").notna().sum()),
        "decisions": out["decision"].value_counts().to_dict(),
        "smart_money_enabled": False,
        "live_order_execution_enabled": False,
    }
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print("V20.4.1_ETF_VALIDATED_OK", audit)


if __name__ == "__main__":
    main()
