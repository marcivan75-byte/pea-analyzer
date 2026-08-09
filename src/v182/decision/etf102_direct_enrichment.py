from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
IN = ROOT / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv"
OUT = ROOT / "outputs/V20.4.3_ETF102_DIRECT_ENRICHED.csv"
AUDIT = ROOT / "outputs/audit/V20.4.3_ETF102_DIRECT_ENRICHMENT.json"


def _float(value) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _positive(value) -> float | None:
    x = _float(value)
    return x if x is not None and x > 0 else None


def _expense_pct(info: dict) -> float | None:
    for key in ("annualReportExpenseRatio", "netExpenseRatio", "expenseRatio"):
        value = _positive(info.get(key))
        if value is None:
            continue
        # Yahoo normally exposes ratios as fractions (0.0025 = 0.25%).
        return value * 100.0 if value <= 1.0 else value
    return None


def _rating(info: dict) -> float | None:
    for key in ("morningStarOverallRating", "morningStarRiskRating"):
        x = _float(info.get(key))
        if x is not None and 0 <= x <= 5:
            return x
    return None


def _spread_pct(info: dict) -> float | None:
    bid, ask = _positive(info.get("bid")), _positive(info.get("ask"))
    if bid is None or ask is None or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return None if mid <= 0 else (ask - bid) / mid * 100.0


def _funds_diversification(ticker) -> tuple[float | None, float | None, float | None]:
    """Return diversification score, top-holdings concentration and sector HHI.

    Only published yfinance fund metadata are used. Missing fund metadata stays
    missing; no neutral 50 is manufactured.
    """
    try:
        fd = ticker.funds_data
    except Exception:
        return None, None, None

    top_concentration = None
    sector_hhi = None
    try:
        top = fd.top_holdings
        if isinstance(top, pd.DataFrame) and not top.empty:
            candidates = [c for c in top.columns if "percent" in str(c).lower() or "weight" in str(c).lower()]
            if candidates:
                vals = pd.to_numeric(top[candidates[0]], errors="coerce").dropna().clip(lower=0)
                if not vals.empty:
                    total = float(vals.sum())
                    if total <= 1.5:
                        total *= 100.0
                    top_concentration = min(100.0, max(0.0, total))
    except Exception:
        pass

    try:
        sectors = fd.sector_weightings
        values: list[float] = []
        if isinstance(sectors, dict):
            values = [_float(v) for v in sectors.values()]
        elif isinstance(sectors, pd.Series):
            values = [_float(v) for v in sectors.tolist()]
        values = [v for v in values if v is not None and v >= 0]
        if values:
            if max(values) > 1.5:
                values = [v / 100.0 for v in values]
            sector_hhi = float(sum(v * v for v in values))
    except Exception:
        pass

    pieces = []
    if top_concentration is not None:
        pieces.append((100.0 - top_concentration, 0.60))
    if sector_hhi is not None:
        pieces.append(((1.0 - min(1.0, sector_hhi)) * 100.0, 0.40))
    if not pieces:
        return None, top_concentration, sector_hhi
    score = sum(v * w for v, w in pieces) / sum(w for _, w in pieces)
    return round(float(np.clip(score, 0, 100)), 2), top_concentration, sector_hhi


def _currency_from(info: dict, row: pd.Series) -> str:
    for value in (info.get("financialCurrency"), info.get("currency"), row.get("trading_currency"), row.get("currency")):
        s = str(value or "").strip().upper()
        if len(s) == 3:
            return s
    return ""


def _fx_to_eur(currencies: set[str]) -> dict[str, float]:
    import yfinance as yf

    rates = {"EUR": 1.0}
    for ccy in sorted(currencies - {"", "EUR"}):
        try:
            pair = yf.Ticker(f"{ccy}EUR=X")
            value = _positive(pair.fast_info.get("last_price"))
            if value is None:
                value = _positive((pair.get_info() or {}).get("regularMarketPrice"))
            if value is not None:
                rates[ccy] = value
        except Exception:
            continue
    return rates


def enrich(df: pd.DataFrame, delay_seconds: float = 0.20) -> tuple[pd.DataFrame, dict]:
    if len(df) != 102 or df["isin"].astype(str).nunique() != 102:
        raise RuntimeError("ETF102 direct enrichment requires exactly 102 unique ISIN")
    if "ticker_identity_status" not in df or not df["ticker_identity_status"].astype(str).eq("FINAL_VALIDATED").all():
        raise RuntimeError("ETF102 identity gate failed")

    import yfinance as yf

    out = df.copy()
    records: list[dict] = []
    failures: list[dict] = []
    currencies: set[str] = set()
    now = datetime.now(timezone.utc).isoformat()

    for _, row in out.iterrows():
        isin = str(row.get("isin") or "").strip().upper()
        ticker_name = str(row.get("yahoo_ticker") or row.get("ticker_yahoo_final") or row.get("ticker_primary") or "").strip()
        if not ticker_name:
            failures.append({"isin": isin, "reason": "MISSING_VALIDATED_TICKER"})
            records.append({"isin": isin})
            continue
        try:
            ticker = yf.Ticker(ticker_name)
            info = ticker.get_info() or {}
            ccy = _currency_from(info, row)
            currencies.add(ccy)
            div_score, top10, sector_hhi = _funds_diversification(ticker)
            records.append({
                "isin": isin,
                "direct_ticker": ticker_name,
                "direct_currency": ccy,
                "direct_total_assets_raw": _positive(info.get("totalAssets")),
                "direct_ter_pct": _expense_pct(info),
                "direct_morningstar_rating": _rating(info),
                "direct_spread_pct": _spread_pct(info),
                "direct_beta3y": _float(info.get("beta3Year")),
                "direct_nav": _positive(info.get("navPrice")),
                "direct_diversification_score": div_score,
                "direct_top_holdings_concentration_pct": top10,
                "direct_sector_hhi": sector_hhi,
                "direct_source": "YFINANCE_FUND_METADATA",
                "direct_collected_at_utc": now,
            })
        except Exception as exc:
            failures.append({"isin": isin, "ticker": ticker_name, "reason": type(exc).__name__})
            records.append({"isin": isin, "direct_ticker": ticker_name, "direct_collected_at_utc": now})
        if delay_seconds:
            time.sleep(max(0.0, delay_seconds))

    direct = pd.DataFrame(records).drop_duplicates("isin", keep="last")
    fx = _fx_to_eur(currencies)
    if not direct.empty:
        direct["direct_fx_to_eur"] = direct["direct_currency"].map(fx)
        direct["direct_aum_eur_m"] = pd.to_numeric(direct["direct_total_assets_raw"], errors="coerce") * pd.to_numeric(direct["direct_fx_to_eur"], errors="coerce") / 1_000_000.0
    out = out.merge(direct, on="isin", how="left")

    # Direct observations fill only genuinely missing legacy fields.
    fill_pairs = {
        "ter_pct": "direct_ter_pct",
        "morningstar_rating": "direct_morningstar_rating",
        "spread_pct": "direct_spread_pct",
        "fund_total_assets_eur_m": "direct_aum_eur_m",
        "diversification_direct_score": "direct_diversification_score",
    }
    for target, source in fill_pairs.items():
        if target not in out.columns:
            out[target] = np.nan
        old = pd.to_numeric(out[target], errors="coerce")
        new = pd.to_numeric(out[source], errors="coerce")
        out[target] = old.where(old.notna(), new)

    fields = [
        "ter_pct", "fund_total_assets_eur_m", "morningstar_rating", "spread_pct",
        "diversification_direct_score", "tracking_error_1y_pct", "tracking_error_3y_pct",
        "tracking_error_5y_pct", "perf_1m_pct", "perf_3m_pct", "perf_6m_pct", "perf_1y_pct",
        "relative_strength", "macd_hist", "rsi14", "rvol20", "volatility_20d",
        "volatility_60d", "max_drawdown_1y", "volume",
    ]
    coverage = {f: int(pd.to_numeric(out.get(f), errors="coerce").notna().sum()) if f in out else 0 for f in fields}
    audit = {
        "passed": True,
        "version": "V20.4.3_ETF102",
        "rows": len(out),
        "unique_isin": int(out["isin"].nunique()),
        "legacy_266_used": False,
        "direct_metadata_success": int(direct.get("direct_total_assets_raw", pd.Series(dtype=float)).notna().sum()) if not direct.empty else 0,
        "failures": failures[:102],
        "fx_to_eur": fx,
        "coverage_count_of_102": coverage,
        "missing_data_policy": "NO_NEUTRAL_50",
    }
    return out, audit


def main() -> None:
    if not IN.exists():
        raise RuntimeError(f"Missing ETF102 master: {IN}")
    df = pd.read_csv(IN, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    out, audit = enrich(df)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, sep=";", index=False, encoding="utf-8-sig")
    AUDIT.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print("V20.4.3_ETF102_DIRECT_ENRICHMENT_OK", json.dumps({"rows": len(out), "coverage": audit["coverage_count_of_102"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
