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


def _text(value) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    s = str(value).strip()
    return s or None


def _expense_pct(info: dict) -> float | None:
    """Normalize Yahoo expense ratios to percentage points."""
    for key in ("annualReportExpenseRatio", "netExpenseRatio", "expenseRatio"):
        raw = _positive(info.get(key))
        if raw is None:
            continue
        pct = raw * 100.0 if raw <= 0.02 else raw
        if 0.0 < pct <= 5.0:
            return round(pct, 6)
    return None


def _yield_pct(info: dict) -> float | None:
    """Return an ETF distribution yield in percentage points when Yahoo exposes it."""
    for key in ("yield", "trailingAnnualDividendYield", "dividendYield"):
        raw = _positive(info.get(key))
        if raw is None:
            continue
        pct = raw * 100.0 if raw <= 1.0 else raw
        if 0.0 < pct <= 30.0:
            return round(pct, 6)
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


def _candidate_number(info: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _positive(info.get(key))
        if value is not None:
            return value
    return None


def _candidate_text(info: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _text(info.get(key))
        if value:
            return value
    return None


def _funds_diversification(ticker) -> tuple[float | None, float | None, float | None]:
    """Return diversification score, top-holdings concentration and sector HHI."""
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


def _price_metrics(close: pd.Series) -> dict:
    close = pd.to_numeric(close, errors="coerce").dropna()
    if close.empty:
        return {}
    close = close[~close.index.duplicated(keep="last")].sort_index()
    try:
        idx = pd.to_datetime(close.index, utc=True).tz_convert(None)
    except Exception:
        idx = pd.to_datetime(close.index).tz_localize(None)
    close.index = idx
    asof = close.index.max()
    result: dict[str, float | str] = {"direct_history_asof": asof.date().isoformat()}

    def perf_years(years: int) -> float | None:
        target = asof - pd.DateOffset(years=years)
        before = close.loc[:target]
        after = close.loc[target:]
        base = None
        base_date = None
        if not before.empty:
            base, base_date = float(before.iloc[-1]), before.index[-1]
        elif not after.empty:
            base, base_date = float(after.iloc[0]), after.index[0]
        if base is None or base <= 0 or base_date is None:
            return None
        if abs((pd.Timestamp(base_date) - pd.Timestamp(target)).days) > 20:
            return None
        return (float(close.iloc[-1]) / base - 1.0) * 100.0

    for years, field in ((1, "direct_perf_1y_pct"), (3, "direct_perf_3y_pct"), (5, "direct_perf_5y_pct")):
        value = perf_years(years)
        if value is not None and math.isfinite(value):
            result[field] = round(value, 6)

    trailing = close.loc[asof - pd.Timedelta(days=370):]
    returns = trailing.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if len(returns) >= 120:
        vol = float(returns.std(ddof=1) * np.sqrt(252.0) * 100.0)
        if math.isfinite(vol):
            result["direct_volatility_1y_pct"] = round(vol, 6)
    if len(trailing) >= 20:
        running_max = trailing.cummax()
        drawdown = trailing / running_max - 1.0
        mdd = float(drawdown.min() * 100.0)
        if math.isfinite(mdd):
            result["direct_max_drawdown_1y_pct"] = round(mdd, 6)
    return result


def _bulk_price_metrics(tickers: list[str]) -> dict[str, dict]:
    import yfinance as yf

    unique = sorted({t for t in tickers if t})
    if not unique:
        return {}
    try:
        raw = yf.download(
            tickers=unique,
            period="5y",
            interval="1d",
            auto_adjust=True,
            actions=False,
            group_by="ticker",
            threads=True,
            progress=False,
            timeout=20,
        )
    except Exception:
        return {}
    result: dict[str, dict] = {}
    if raw is None or raw.empty:
        return result
    for ticker_name in unique:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                level0 = raw.columns.get_level_values(0)
                level1 = raw.columns.get_level_values(1)
                if ticker_name in level0:
                    sub = raw[ticker_name]
                elif ticker_name in level1:
                    sub = raw.xs(ticker_name, axis=1, level=1)
                else:
                    continue
            else:
                sub = raw
            if "Close" not in sub.columns:
                continue
            result[ticker_name] = _price_metrics(sub["Close"])
        except Exception:
            continue
    return result


def _fill_text(out: pd.DataFrame, target: str, source: str) -> None:
    if target not in out.columns:
        out[target] = pd.NA
    if source not in out.columns:
        return
    old = out[target].astype("string")
    new = out[source].astype("string")
    missing = old.isna() | old.str.strip().fillna("").eq("")
    valid_new = new.notna() & new.str.strip().fillna("").ne("")
    out.loc[missing & valid_new, target] = new[missing & valid_new]


def _fill_numeric(out: pd.DataFrame, target: str, source: str) -> None:
    if target not in out.columns:
        out[target] = np.nan
    if source not in out.columns:
        return
    old = pd.to_numeric(out[target], errors="coerce")
    new = pd.to_numeric(out[source], errors="coerce")
    out[target] = old.where(old.notna(), new)


def _derive_peer_ranks(out: pd.DataFrame) -> None:
    peer = out.get("morningstar_category", pd.Series(index=out.index, dtype=object)).astype("string")
    category = out.get("category", pd.Series(index=out.index, dtype=object)).astype("string")
    peer = peer.where(peer.notna() & peer.str.strip().ne(""), category)
    peer = peer.fillna("UNCLASSIFIED")
    for period in (1, 3, 5):
        perf_col = f"perf_{period}y_pct"
        rank_col = f"rank_cat_{period}y"
        if perf_col not in out.columns:
            continue
        perf = pd.to_numeric(out[perf_col], errors="coerce")
        derived = perf.groupby(peer).rank(method="min", ascending=False)
        if rank_col not in out.columns:
            out[rank_col] = np.nan
        old = pd.to_numeric(out[rank_col], errors="coerce")
        out[rank_col] = old.where(old.notna(), derived)
    out["rank_cat_method"] = "DERIVED_WITHIN_ETF102_PEER_GROUP_WHEN_OFFICIAL_RANK_MISSING"
    out["rank_cat_peer_group"] = peer


def enrich(df: pd.DataFrame, delay_seconds: float = 0.12) -> tuple[pd.DataFrame, dict]:
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
    ticker_names: list[str] = []

    for _, row in out.iterrows():
        isin = str(row.get("isin") or "").strip().upper()
        ticker_name = str(row.get("yahoo_ticker") or row.get("ticker_yahoo_final") or row.get("ticker_primary") or "").strip()
        if ticker_name:
            ticker_names.append(ticker_name)
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
                "direct_dividend_yield_pct": _yield_pct(info),
                "direct_holdings_count": _candidate_number(info, ("holdingsCount", "numberOfHoldings", "totalHoldings")),
                "direct_benchmark": _candidate_text(info, ("benchmark", "benchmarkName", "benchmarkIndex")),
                "direct_distribution_frequency": _candidate_text(info, ("dividendFrequency", "distributionFrequency")),
                "direct_diversification_score": div_score,
                "direct_top_holdings_concentration_pct": top10,
                "direct_sector_hhi": sector_hhi,
                "direct_source": "YFINANCE_FUND_METADATA",
                "direct_source_url": f"https://finance.yahoo.com/quote/{ticker_name}",
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

    history = _bulk_price_metrics(ticker_names)
    if history and not direct.empty:
        hist_df = pd.DataFrame([{"direct_ticker": ticker, **metrics} for ticker, metrics in history.items()])
        direct = direct.merge(hist_df, on="direct_ticker", how="left")

    out = out.merge(direct, on="isin", how="left")

    numeric_fill_pairs = {
        "ter_pct": "direct_ter_pct",
        "morningstar_rating": "direct_morningstar_rating",
        "spread_pct": "direct_spread_pct",
        "fund_total_assets_eur_m": "direct_aum_eur_m",
        "aum_m": "direct_aum_eur_m",
        "diversification_direct_score": "direct_diversification_score",
        "dividend_yield_pct": "direct_dividend_yield_pct",
        "holdings": "direct_holdings_count",
        "perf_1y_pct": "direct_perf_1y_pct",
        "perf_3y_pct": "direct_perf_3y_pct",
        "perf_5y_pct": "direct_perf_5y_pct",
        "volatility_1y_pct": "direct_volatility_1y_pct",
        "max_drawdown_1y_pct": "direct_max_drawdown_1y_pct",
    }
    for target, source in numeric_fill_pairs.items():
        _fill_numeric(out, target, source)

    text_fill_pairs = {
        "official_benchmark": "direct_benchmark",
        "distribution_frequency": "direct_distribution_frequency",
        "source_name": "direct_source",
        "source_url": "direct_source_url",
        "ticker_euronext": "euronext_symbol",
        "ticker_yahoo": "yahoo_ticker",
        "official_exchange": "primary_exchange",
        "ticker_validation_as_of": "ticker_validated_as_of",
    }
    for target, source in text_fill_pairs.items():
        _fill_text(out, target, source)

    if "ticker_validation_wave" not in out.columns:
        out["ticker_validation_wave"] = pd.NA
    wave = out["ticker_validation_wave"].astype("string")
    missing_wave = wave.isna() | wave.str.strip().fillna("").eq("")
    final_identity = out.get("ticker_identity_status", pd.Series(False, index=out.index)).astype(str).eq("FINAL_VALIDATED")
    out.loc[missing_wave & final_identity, "ticker_validation_wave"] = "V20.5_REFERENCE_CONSOLIDATION"

    if "perf_as_of" not in out.columns:
        out["perf_as_of"] = pd.NA
    if "direct_history_asof" in out.columns:
        pa = out["perf_as_of"].astype("string")
        ha = out["direct_history_asof"].astype("string")
        mask = (pa.isna() | pa.str.strip().fillna("").eq("")) & ha.notna() & ha.str.strip().fillna("").ne("")
        out.loc[mask, "perf_as_of"] = ha[mask]

    _derive_peer_ranks(out)

    ter = pd.to_numeric(out.get("ter_pct"), errors="coerce")
    if bool((ter.dropna() > 5.0).any()):
        raise RuntimeError("ETF102 TER unit gate failed: value above 5%")

    fields = [
        "dividend_yield_pct", "ter_pct", "aum_m", "fund_total_assets_eur_m", "holdings",
        "morningstar_rating", "official_benchmark", "distribution_frequency", "official_exchange",
        "spread_pct", "diversification_direct_score", "tracking_error_1y_pct", "tracking_error_3y_pct",
        "tracking_error_5y_pct", "perf_1m_pct", "perf_3m_pct", "perf_6m_pct", "perf_1y_pct",
        "perf_3y_pct", "perf_5y_pct", "rank_cat_1y", "rank_cat_3y", "rank_cat_5y",
        "relative_strength", "macd_hist", "rsi14", "rvol20", "volatility_20d",
        "volatility_60d", "volatility_1y_pct", "max_drawdown_1y", "max_drawdown_1y_pct", "volume",
    ]
    coverage = {}
    for field in fields:
        if field not in out.columns:
            coverage[field] = 0
            continue
        numeric = pd.to_numeric(out[field], errors="coerce")
        if numeric.notna().any() or field in {
            "dividend_yield_pct", "ter_pct", "aum_m", "fund_total_assets_eur_m", "holdings", "morningstar_rating",
            "spread_pct", "diversification_direct_score", "tracking_error_1y_pct", "tracking_error_3y_pct",
            "tracking_error_5y_pct", "perf_1m_pct", "perf_3m_pct", "perf_6m_pct", "perf_1y_pct", "perf_3y_pct",
            "perf_5y_pct", "rank_cat_1y", "rank_cat_3y", "rank_cat_5y", "relative_strength", "macd_hist", "rsi14",
            "rvol20", "volatility_20d", "volatility_60d", "volatility_1y_pct", "max_drawdown_1y", "max_drawdown_1y_pct", "volume"
        }:
            coverage[field] = int(numeric.notna().sum())
        else:
            s = out[field].astype("string")
            coverage[field] = int((s.notna() & s.str.strip().fillna("").ne("")).sum())

    audit = {
        "passed": True,
        "version": "V20.5_ETF102_REFERENCE_ENRICHMENT",
        "rows": len(out),
        "unique_isin": int(out["isin"].nunique()),
        "legacy_266_used": False,
        "direct_metadata_success": int(direct.get("direct_total_assets_raw", pd.Series(dtype=float)).notna().sum()) if not direct.empty else 0,
        "history_metric_tickers": len(history),
        "failures": failures[:102],
        "fx_to_eur": fx,
        "coverage_count_of_102": coverage,
        "ter_pct_stats": {
            "count": int(ter.notna().sum()),
            "min": round(float(ter.min()), 4) if ter.notna().any() else None,
            "median": round(float(ter.median()), 4) if ter.notna().any() else None,
            "max": round(float(ter.max()), 4) if ter.notna().any() else None,
        },
        "missing_data_policy": "NO_NEUTRAL_50",
        "rank_cat_semantics": "OFFICIAL_VALUE_PRESERVED_ELSE_DERIVED_RANK_WITHIN_ETF102_PEER_GROUP",
        "derived_fields": [
            "perf_1y_pct", "perf_3y_pct", "perf_5y_pct", "volatility_1y_pct", "max_drawdown_1y_pct",
            "rank_cat_1y", "rank_cat_3y", "rank_cat_5y"
        ],
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
    print("V20.5_ETF102_DIRECT_ENRICHMENT_OK", json.dumps({"rows": len(out), "coverage": audit["coverage_count_of_102"], "ter": audit["ter_pct_stats"], "history_tickers": audit["history_metric_tickers"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
