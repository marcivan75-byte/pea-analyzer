from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "data/reference/V21.0_ACTIONS_PEA_CONFIG.json"
TARGET = ROOT / "outputs/V21.0_ACTIONS_PEA_1829_PREPARED.csv"
AUDIT = ROOT / "outputs/audit/V21.0_ACTIONS_YAHOO_BACKFILL.json"
MAX_BACKFILL = 850
NORMAL_DELAY_SECONDS = 0.35
RATE_LIMIT_BACKOFF_SECONDS = (10.0, 25.0, 50.0)


def _f(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _pct(value):
    x = _f(value)
    if x is None:
        return None
    return x * 100.0 if abs(x) <= 1.5 else x


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _missing(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(True, index=df.index)
    s = df[col]
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_numeric(s, errors="coerce").isna()
    text = s.astype("string")
    return text.isna() | text.str.strip().fillna("").eq("") | text.str.lower().isin({"nan", "none", "null", "n/a"})


def _fill_missing(df: pd.DataFrame, idx, field: str, value) -> bool:
    if value is None:
        return False
    if field not in df.columns:
        df[field] = np.nan
    current = df.loc[idx, field]
    if isinstance(current, pd.Series):
        mask = _missing(df.loc[idx], field)
        if not bool(mask.any()):
            return False
        df.loc[current.index[mask], field] = value
        return True
    if pd.isna(current) or str(current).strip().lower() in {"", "nan", "none", "null", "n/a"}:
        df.loc[idx, field] = value
        return True
    return False


def _price_match(canonical, observed, tolerance: float = 0.25) -> tuple[bool, float | None]:
    c, o = _f(canonical), _f(observed)
    if c is None or o is None or c <= 0 or o <= 0:
        return False, None
    ratio = o / c
    return (1.0 - tolerance) <= ratio <= (1.0 + tolerance), ratio


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(token in text for token in ("429", "rate limit", "too many requests", "ratelimited"))


def _get_info_with_backoff(ticker, stats: dict):
    last_exc = None
    for attempt in range(len(RATE_LIMIT_BACKOFF_SECONDS) + 1):
        try:
            return ticker.get_info() or {}
        except Exception as exc:
            last_exc = exc
            if not _is_rate_limit(exc):
                raise
            stats["rate_limit_events"] += 1
            if attempt >= len(RATE_LIMIT_BACKOFF_SECONDS):
                break
            cooldown = RATE_LIMIT_BACKOFF_SECONDS[attempt]
            stats["rate_limit_sleep_seconds"] += cooldown
            time.sleep(cooldown)
    if last_exc is not None:
        raise last_exc
    return {}


def _coverage(df: pd.DataFrame, fields: list[str]) -> dict[str, float]:
    return {field: round(float((~_missing(df, field)).mean() * 100.0), 2) for field in fields}


def main() -> None:
    if not TARGET.exists():
        raise RuntimeError(f"missing prepared Actions reference: {TARGET}")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    expected = int(cfg["canonical_universe_size"])
    df = pd.read_csv(TARGET, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != expected or df["isin"].astype(str).nunique() != expected:
        raise RuntimeError(f"Yahoo backfill requires canonical {expected} Actions universe")

    key_fields = [
        "market_cap_v21", "per_forward_v21", "pb_v21", "fcf_yield_v21", "roe_v21_pct",
        "roa_v21_pct", "operating_margin_v21_pct", "revenue_growth_v21_pct", "earnings_growth_v21_pct",
        "debt_to_ebitda_v21", "dividend_yield_v21_pct", "payout_ratio_v21_pct", "target_mean_v21",
        "consensus_score_100_v21", "next_earnings_date",
    ]
    before = _coverage(df, key_fields)

    history_ok = df.get("direct_yahoo_history_applied", pd.Series(False, index=df.index)).astype(str).str.lower().isin({"true", "1", "yes"})
    info_done = df.get("direct_yahoo_info_applied", pd.Series(False, index=df.index)).astype(str).str.lower().isin({"true", "1", "yes"})
    valid_ticker = df["yahoo_ticker"].astype("string").notna() & df["yahoo_ticker"].astype("string").str.strip().fillna("").ne("")
    needs = pd.Series(False, index=df.index)
    for field in key_fields:
        needs |= _missing(df, field)
    candidates = df.index[history_ok & ~info_done & valid_ticker & needs].tolist()
    if "v210_enrichment_priority_score" in df.columns:
        score = pd.to_numeric(df["v210_enrichment_priority_score"], errors="coerce").fillna(-1)
        candidates = sorted(candidates, key=lambda i: float(score.loc[i]), reverse=True)
    candidates = candidates[:MAX_BACKFILL]

    mappings = {
        "marketCap": "market_cap_v21", "enterpriseValue": "enterprise_value_v21", "forwardPE": "per_forward_v21",
        "trailingPE": "per_ttm_v21", "priceToBook": "pb_v21", "freeCashflow": "free_cash_flow_v21",
        "operatingCashflow": "operating_cash_flow_v21", "totalDebt": "total_debt_v21", "totalCash": "total_cash_v21",
        "ebitda": "ebitda_v21", "debtToEquity": "debt_to_equity_v21", "currentRatio": "current_ratio_v21",
        "beta": "beta_v21", "targetMeanPrice": "target_mean_v21", "targetMedianPrice": "target_median_v21",
        "targetLowPrice": "target_low_v21", "targetHighPrice": "target_high_v21", "numberOfAnalystOpinions": "n_analysts_v21",
        "shortRatio": "short_ratio", "forwardEps": "forward_eps_v21", "trailingEps": "trailing_eps_v21",
        "pegRatio": "peg_ratio_v21", "revenuePerShare": "revenue_per_share_v21",
    }
    pct_mappings = {
        "returnOnEquity": "roe_v21_pct", "returnOnAssets": "roa_v21_pct", "operatingMargins": "operating_margin_v21_pct",
        "profitMargins": "net_margin_v21_pct", "grossMargins": "gross_margin_v21_pct", "revenueGrowth": "revenue_growth_v21_pct",
        "earningsGrowth": "earnings_growth_v21_pct", "earningsQuarterlyGrowth": "earnings_quarterly_growth_v21_pct",
        "dividendYield": "dividend_yield_v21_pct", "payoutRatio": "payout_ratio_v21_pct",
        "heldPercentInstitutions": "institutional_ownership_pct", "heldPercentInsiders": "insider_ownership_pct",
        "shortPercentOfFloat": "short_percent_float_pct",
    }

    stats = {"attempted": 0, "applied": 0, "identity_rejected": 0, "provider_errors": 0,
             "rate_limit_events": 0, "rate_limit_sleep_seconds": 0.0, "filled_cells": 0}
    errors = []
    import yfinance as yf

    for i in candidates:
        ticker_name = str(df.at[i, "yahoo_ticker"]).strip()
        if not ticker_name:
            continue
        stats["attempted"] += 1
        try:
            info = _get_info_with_backoff(yf.Ticker(ticker_name), stats)
            canonical = _f(df.at[i, "last_close"] if "last_close" in df.columns else None)
            observed = _f(info.get("currentPrice")) or _f(info.get("regularMarketPrice")) or _f(info.get("previousClose"))
            matched, ratio = _price_match(canonical, observed)
            if observed is not None and canonical is not None and not matched:
                stats["identity_rejected"] += 1
                errors.append({"ticker": ticker_name, "reason": "PRICE_MISMATCH", "ratio": ratio})
                continue

            filled = 0
            for src, dst in mappings.items():
                if _fill_missing(df, i, dst, _f(info.get(src))): filled += 1
            for src, dst in pct_mappings.items():
                if _fill_missing(df, i, dst, _pct(info.get(src))): filled += 1

            rec = _f(info.get("recommendationMean"))
            if rec is not None:
                score100 = max(0.0, min(100.0, (5.0 - rec) / 4.0 * 100.0))
                if _fill_missing(df, i, "consensus_score_100_v21", score100): filled += 1
            label = info.get("recommendationKey")
            if label and _fill_missing(df, i, "consensus_label_v21", str(label).upper()): filled += 1

            ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
            if ts and _missing(df.loc[[i]], "next_earnings_date").iloc[0]:
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                df.at[i, "next_earnings_date"] = dt.date().isoformat()
                days = (dt.date() - datetime.now(timezone.utc).date()).days
                df.at[i, "days_to_earnings"] = days
                df.at[i, "earnings_window_7d_flag"] = bool(0 <= days <= 7)
                df.at[i, "earnings_window_30d_flag"] = bool(0 <= days <= 30)
                filled += 1

            mc = _f(df.at[i, "market_cap_v21"] if "market_cap_v21" in df.columns else None)
            fcf = _f(df.at[i, "free_cash_flow_v21"] if "free_cash_flow_v21" in df.columns else None)
            debt = _f(df.at[i, "total_debt_v21"] if "total_debt_v21" in df.columns else None)
            ebitda = _f(df.at[i, "ebitda_v21"] if "ebitda_v21" in df.columns else None)
            if mc and fcf is not None and _fill_missing(df, i, "fcf_yield_v21", fcf / mc * 100.0): filled += 1
            if debt is not None and ebitda and _fill_missing(df, i, "debt_to_ebitda_v21", debt / ebitda): filled += 1

            if filled:
                stats["applied"] += 1; stats["filled_cells"] += filled
                df.at[i, "yahoo_backfill_status"] = "APPLIED_PRICE_MATCH" if matched else "APPLIED_HISTORY_VALIDATED"
            else:
                df.at[i, "yahoo_backfill_status"] = "NO_NEW_FIELDS"
            time.sleep(NORMAL_DELAY_SECONDS)
        except Exception as exc:
            stats["provider_errors"] += 1
            errors.append({"ticker": ticker_name, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"})
            time.sleep(NORMAL_DELAY_SECONDS)

    last = _num(df, "last_close"); target = _num(df, "target_mean_v21")
    df["target_upside_pct_v21"] = ((target / last) - 1.0) * 100.0
    df.loc[last.le(0) | last.isna() | target.isna(), "target_upside_pct_v21"] = np.nan
    df["potential_gt_15_flag"] = df["target_upside_pct_v21"].ge(15).where(df["target_upside_pct_v21"].notna())

    after = _coverage(df, key_fields)
    df.to_csv(TARGET, sep=";", index=False, encoding="utf-8-sig")
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    audit = {
        "passed": True, "rows": len(df), "expected_rows": expected, "candidate_pool": len(candidates), "max_backfill": MAX_BACKFILL,
        **stats, "coverage_before_pct": before, "coverage_after_pct": after,
        "coverage_gain_points": {k: round(after[k] - before[k], 2) for k in key_fields},
        "errors_sample": errors[:50], "missing_data_policy": "FILL_GAPS_ONLY_NO_OVERWRITE",
        "identity_policy": "HISTORY_VALIDATED_AND_CURRENT_PRICE_MUST_NOT_CONTRADICT",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print("V21_ACTIONS_YAHOO_BACKFILL_1829_OK", json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
