"""Compatibility adapter for the PEA Analyzer Free Capture snapshot.

The repository's V21.1 Free Capture is a very wide, semicolon-delimited snapshot.
The TCT application deliberately keeps its own small internal contract.  This
module performs only deterministic, auditable mappings between the two.

Important: an exact T1/T2 setup is *not* inferred from a single snapshot.  If
Free Capture does not provide a proven setup, ``setup`` remains empty and its
bonus remains zero.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from src.signals.earnings_proximity import score_earnings_proximity


REPO_MARKERS = {
    "V21.1": {"isin", "last_close", "yahoo_ticker", "pea_confidence"},
}


def _numeric(df: pd.DataFrame, *cols: str) -> pd.Series:
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for col in cols:
        if col in df.columns:
            x = pd.to_numeric(df[col], errors="coerce")
            out = out.where(out.notna(), x)
    return out


def _text(df: pd.DataFrame, *cols: str) -> pd.Series:
    out = pd.Series(pd.NA, index=df.index, dtype="string")
    for col in cols:
        if col not in df.columns:
            continue
        x = df[col].astype("string").str.strip()
        x = x.mask(x.str.lower().isin({"", "nan", "none", "null", "<na>"}))
        out = out.where(out.notna(), x)
    return out


def _truth(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    s = df[col].astype("string").str.strip().str.lower()
    observed = ~s.isin({"", "nan", "none", "null", "<na>"}) & s.notna()
    truth = s.isin({"true", "1", "yes", "oui", "y", "pass"}).astype(float)
    return truth.where(observed)


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    den = pd.to_numeric(den, errors="coerce")
    num = pd.to_numeric(num, errors="coerce")
    return (num / den).where(den.abs() > 1e-12)


def _rank_score(s: pd.Series, higher_is_better: bool = True) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    if x.notna().sum() < 2:
        return pd.Series(np.nan, index=x.index, dtype=float)
    p = x.rank(method="average", pct=True) * 100.0
    return p if higher_is_better else 100.0 - p


def _technical_proxy(out: pd.DataFrame) -> pd.Series:
    """Transparent snapshot technical score; not an exact T1/T2 detector."""
    idx = out.index
    parts: list[pd.Series] = []

    rsi = _numeric(out, "rsi", "rsi14")
    rsi_score = 100.0 - (rsi - 60.0).abs() * 2.5
    parts.append(rsi_score.clip(0, 100))

    breakout = _truth(out, "breakout_20d_flag")
    if breakout.notna().any():
        parts.append(breakout.map({1.0: 100.0, 0.0: 30.0}))

    macd = _numeric(out, "macd")
    macd_signal = _numeric(out, "macd_signal")
    macd_score = pd.Series(np.nan, index=idx, dtype=float)
    obs = macd.notna() & macd_signal.notna()
    macd_score.loc[obs] = np.where(macd.loc[obs] > macd_signal.loc[obs], 75.0, 40.0)
    parts.append(macd_score)

    above20 = _truth(out, "above_mm20")
    above50 = _truth(out, "above_mm50")
    trend = pd.concat([above20, above50], axis=1).mean(axis=1, skipna=True) * 100.0
    trend = trend.where(pd.concat([above20, above50], axis=1).notna().any(axis=1))
    parts.append(trend)

    rvol = _numeric(out, "vol_ratio", "rvol20")
    parts.append((rvol / 2.5 * 100.0).clip(0, 100))

    frame = pd.concat(parts, axis=1)
    return frame.mean(axis=1, skipna=True).where(frame.notna().any(axis=1))


def _pea_proof(out: pd.DataFrame) -> pd.Series:
    # V21.1 already separates PASS from REVIEW_ONLY.  Preserve that governance:
    # PASS => eligible proof, REVIEW_ONLY => unknown/quarantine, never auto-pass.
    gate = _text(out, "pea_validation_gate").str.upper()
    conf = _text(out, "pea_confidence").str.upper()
    proof = pd.Series("UNKNOWN", index=out.index, dtype="string")
    proof.loc[gate.eq("PASS")] = "PASS"
    proof.loc[gate.isna() & conf.str.startswith("HIGH", na=False)] = "PASS"
    return proof


def _normalize_earnings_days(out: pd.DataFrame) -> pd.Series:
    days = _numeric(out, "days_to_earnings")
    # Negative values in V21.1 denote a publication already in the past; they
    # must not be interpreted as J-1 by the execution gate.
    days = days.mask(days < 0)
    return days


def is_repo_free_capture(df: pd.DataFrame) -> bool:
    if df is None or df.empty:
        return False
    cols = set(df.columns)
    return any(markers.issubset(cols) for markers in REPO_MARKERS.values())


def adapt_repo_free_capture(df: pd.DataFrame) -> pd.DataFrame:
    """Map the repository V21.1 Free Capture snapshot to the TCT internal schema.

    All original columns are retained.  Derived columns have an explicit
    ``tct_adapter_*`` provenance marker where ambiguity could matter.
    """
    if df is None or df.empty:
        return df
    if not is_repo_free_capture(df):
        return df.copy()

    out = df.copy()

    # Asset/source provenance. The current repo exposes two canonical PEA
    # contracts: V21.1 Actions and V20.7 ETF102. Both share enough market
    # fields for the TCT snapshot adapter, but they must remain distinguishable
    # downstream.
    asset = _text(out, "asset_class").str.upper()
    pea_type = _text(out, "pea_type").str.upper()
    is_etf = asset.eq("ETF") | pea_type.str.contains("ETF", na=False)
    out["tct_asset_class"] = pd.Series(
        np.where(is_etf.fillna(False), "ETF", "ACTION"),
        index=out.index, dtype="string"
    )
    out["tct_adapter_source"] = pd.Series(
        np.where(
            is_etf.fillna(False),
            "V20.7_ETF102_REFERENCE_MASTER",
            "V21.1_ACTIONS_PEA_REFERENCE_MERGED",
        ),
        index=out.index, dtype="string"
    )

    # Identity / eligibility -------------------------------------------------
    if "ticker" not in out.columns:
        out["ticker"] = _text(out, "yahoo_ticker", "v182_ticker_market_symbol", "euronext_symbol")
    if "symbol" not in out.columns:
        out["symbol"] = _text(out, "v182_ticker_market_symbol", "euronext_symbol", "yahoo_ticker")
    if "pea_proof_level" not in out.columns:
        out["pea_proof_level"] = _pea_proof(out)
    if "pea_eligible" not in out.columns:
        out["pea_eligible"] = out["pea_proof_level"].astype("string").str.upper().eq("PASS")

    # Core execution inputs -------------------------------------------------
    if "close" not in out.columns:
        out["close"] = _numeric(out, "last_close", "current_price_yf")
    else:
        out["close"] = pd.to_numeric(out["close"], errors="coerce")

    if "days_to_earnings" in out.columns:
        out["days_to_earnings_raw"] = pd.to_numeric(out["days_to_earnings"], errors="coerce")
    out["days_to_earnings"] = _normalize_earnings_days(out)

    if "avg_dollar_volume_20d" not in out.columns:
        avg_volume = _numeric(out, "volume_avg_20d")
        out["avg_dollar_volume_20d"] = out["close"] * avg_volume
        # Fallback from today's volume / RVOL where the 20d average itself is absent.
        current_volume = _numeric(out, "volume")
        rvol = _numeric(out, "rvol20")
        inferred_avg = _safe_div(current_volume, rvol).where(rvol > 0)
        fallback_adv = out["close"] * inferred_avg
        out["avg_dollar_volume_20d"] = out["avg_dollar_volume_20d"].where(
            out["avg_dollar_volume_20d"].notna(), fallback_adv
        )
    else:
        out["avg_dollar_volume_20d"] = pd.to_numeric(out["avg_dollar_volume_20d"], errors="coerce")

    # Exact T1/T2 cannot be proven from one snapshot. -----------------------
    if "setup" not in out.columns:
        out["setup"] = pd.Series([None] * len(out), index=out.index, dtype=object)
        out["setup_source"] = "UNCONFIRMED_SINGLE_SNAPSHOT"
    else:
        setup = out["setup"].astype("string")
        out["setup_source"] = np.where(setup.notna(), "UPSTREAM", "UNCONFIRMED_SINGLE_SNAPSHOT")
        out["setup"] = setup.astype(object).where(setup.notna(), None)
    if "bonus" not in out.columns:
        out["bonus"] = 0.0

    # Technical aliases / deterministic derivations -------------------------
    if "rsi" not in out.columns:
        out["rsi"] = _numeric(out, "rsi14")
    if "vol_ratio" not in out.columns:
        out["vol_ratio"] = _numeric(out, "rvol20")
        vol = _numeric(out, "volume")
        avg = _numeric(out, "volume_avg_20d")
        out["vol_ratio"] = out["vol_ratio"].where(out["vol_ratio"].notna(), _safe_div(vol, avg))

    upper = _numeric(out, "bb_upper")
    lower = _numeric(out, "bb_lower")
    mid = _numeric(out, "bb_mid")
    if "bandwidth" not in out.columns:
        out["bandwidth"] = _safe_div(upper - lower, mid).where(mid > 0)
    if "percent_b" not in out.columns:
        out["percent_b"] = _safe_div(out["close"] - lower, upper - lower)
    if "atr_pct" not in out.columns:
        out["atr_pct"] = _safe_div(_numeric(out, "atr14"), out["close"]).where(out["close"] > 0)

    if "market_cap_m" not in out.columns:
        out["market_cap_m"] = _numeric(out, "market_cap_v21", "market_cap") / 1_000_000.0
    if "shares_outstanding" not in out.columns:
        market_cap = _numeric(out, "market_cap_v21", "market_cap")
        out["shares_outstanding"] = _safe_div(market_cap, out["close"]).where(out["close"] > 0)

    if "short_interest" not in out.columns:
        out["short_interest"] = _numeric(out, "short_percent_float_pct", "public_short_pct")
    if "short_n_holders" not in out.columns and "short_holders" in out.columns:
        out["short_n_holders"] = _numeric(out, "short_holders")

    # Scoring aliases.  Only map values already on a 0-100-like scale or an
    # explicitly documented percentile; never turn an arbitrary raw value into
    # a fake probability.
    aliases = {
        "score_valo": ("valuation_discount_score",),
        "score_news": ("news_catalyst_score", "funnel_instrument_news_score"),
        "score_regime": ("action_topdown_score",),
    }
    for dst, srcs in aliases.items():
        if dst not in out.columns:
            out[dst] = _numeric(out, *srcs).clip(0, 100)

    if "score_rs" not in out.columns:
        out["score_rs"] = _rank_score(_numeric(out, "relative_strength", "perf_1m_pct"), higher_is_better=True)
    if "score_t1_tech" not in out.columns:
        out["score_t1_tech"] = _technical_proxy(out).clip(0, 100)

    if "score_cata" not in out.columns:
        cata_parts = []
        for col in ("guidance_revision_score", "regulatory_catalyst_score", "major_contract_score"):
            if col in out.columns:
                cata_parts.append(pd.to_numeric(out[col], errors="coerce").clip(0, 100))
        if "mna_rumor_score" in out.columns:
            # Rumor-only evidence is intentionally capped.
            cata_parts.append(pd.to_numeric(out["mna_rumor_score"], errors="coerce").clip(0, 65))
        if cata_parts:
            out["score_cata"] = pd.concat(cata_parts, axis=1).max(axis=1, skipna=True)
            out["score_cata"] = out["score_cata"].where(pd.concat(cata_parts, axis=1).notna().any(axis=1))
        else:
            out["score_cata"] = np.nan

    if "score_earnings_proximity" not in out.columns:
        eps = _numeric(out, "eps_revision_3m")
        beat = _numeric(out, "beat_rate")
        short = _numeric(out, "short_interest")
        out["score_earnings_proximity"] = [
            score_earnings_proximity(d, e, b, s)
            for d, e, b, s in zip(out["days_to_earnings"], eps, beat, short)
        ]

    if "tct_market_bucket" not in out.columns and "sector_bucket" in out.columns:
        out["tct_market_bucket"] = _text(out, "sector_bucket")
    if "sector" not in out.columns:
        # ``sector_bucket`` is a country/eligibility bucket, not an economic
        # sector. Never feed it to the 11-sector dashboard.
        out["sector"] = _text(out, "sector_v21", "sector_yahoo", "sector_yf")
    if "secteur" not in out.columns:
        out["secteur"] = out["sector"].astype("string").fillna("UNCLASSIFIED")

    # A real calibrated probability may be passed by a later upstream module.
    # Do not derive one from score_ct or any ranking score.
    if "meta_proba" not in out.columns:
        for candidate in ("tct_probability_20d_calibrated_v212", "probability_20d_calibrated"):
            if candidate in out.columns:
                p = pd.to_numeric(out[candidate], errors="coerce")
                if p.dropna().between(0, 1).all():
                    out["meta_proba"] = p
                    break

    return out
