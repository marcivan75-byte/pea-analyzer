from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd

from v182.sources.yfinance_bulk import download_history, DownloadResult
from v182.sources.yfinance_info import collect_info, FIELDS as INFO_FIELDS
from v182.features.ohlcv_features import calculate as calculate_features
from v182.io.frames import is_missing

NOW = lambda: datetime.now(timezone.utc).isoformat()


def _obs(universe: str, isin: str, field: str, value, source: str, evidence: str) -> dict:
    return {
        "universe": universe,
        "isin": isin,
        "field": field,
        "value": value,
        "source": source,
        "collected_at": NOW(),
        "as_of": NOW()[:10],
        "evidence_level": evidence,
        "validation_status": "AUTO_MATCH",
    }


# ---------------------------------------------------------------- WAVE 01/02
def wave_history(df: pd.DataFrame, universe: str, cache_dir: str, cfg: dict) -> DownloadResult:
    """Wave 01 (Actions) / Wave 02 (ETF) : téléchargement bulk OHLCV 5 ans."""
    valid = df[df["yahoo_ticker"].apply(lambda v: not is_missing(v))]
    tickers = valid["yahoo_ticker"].tolist()
    batch_key = "actions_batch_size" if universe == "ACTION" else "etf_batch_size"
    return download_history(
        tickers=tickers,
        cache_dir=cache_dir,
        period=cfg["yfinance"]["history_period"],
        interval=cfg["yfinance"]["interval"],
        batch_size=cfg["yfinance"][batch_key],
        auto_adjust=cfg["yfinance"]["auto_adjust"],
    )


def resolve_etf_tickers(etf_df: pd.DataFrame, mapping_path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Le référentiel ETF n'a pas de colonne yahoo_ticker native : on la
    complète depuis une table de correspondance ISIN -> ticker maintenue à
    part (config/V18.2_ETF_TICKER_MAP.csv, colonnes isin;yahoo_ticker).
    Les ISIN sans correspondance sont renvoyés séparément (INPUT_REQUIRED).
    """
    mapping_file = Path(mapping_path)
    if mapping_file.exists():
        mapping = pd.read_csv(mapping_file, sep=";", encoding="utf-8-sig", dtype=str)
        mapping = mapping[[c for c in ["isin", "yahoo_ticker"] if c in mapping.columns]].drop_duplicates("isin")
    else:
        mapping = pd.DataFrame(columns=["isin", "yahoo_ticker"])

    merged = etf_df.copy()
    native = merged["yahoo_ticker"].copy() if "yahoo_ticker" in merged.columns else pd.Series(pd.NA, index=merged.index)
    merged = merged.drop(columns=["yahoo_ticker"], errors="ignore").merge(mapping, on="isin", how="left")
    merged["yahoo_ticker"] = merged["yahoo_ticker"].where(~merged["yahoo_ticker"].apply(is_missing), native.values)
    gaps = merged[merged["yahoo_ticker"].apply(is_missing)][["isin", "name"]].copy()
    gaps["status"] = "INPUT_REQUIRED"
    gaps["reason"] = "NO_TICKER_MAPPING"
    return merged, gaps


# ---------------------------------------------------------------- WAVE 03
def wave3_derived_features(cache_dir: str, ticker_isin_map: dict[str, str], universe: str) -> list[dict]:
    """Wave 03 : calcule tous les indicateurs dérivables localement à partir
    des Parquet OHLCV téléchargés en Wave 01/02. Aucun appel réseau ici."""
    observations: list[dict] = []
    per_ticker_perf_1y: dict[str, float] = {}
    per_ticker_indicators: dict[str, dict] = {}

    cache = Path(cache_dir)
    for parquet_file in sorted(cache.glob("history_*.parquet")):
        frame = pd.read_parquet(parquet_file)
        if not hasattr(frame.columns, "levels"):
            continue
        tickers = frame.columns.get_level_values(0).unique()
        for ticker in tickers:
            isin = ticker_isin_map.get(ticker)
            if isin is None:
                continue
            sub = frame[ticker]
            indicators = calculate_features(sub)
            if not indicators:
                continue
            per_ticker_indicators[isin] = indicators
            if indicators.get("perf_1y_pct") is not None:
                per_ticker_perf_1y[isin] = indicators["perf_1y_pct"]

    median_perf = pd.Series(per_ticker_perf_1y).median() if per_ticker_perf_1y else 0.0
    for isin, indicators in per_ticker_indicators.items():
        for field, value in indicators.items():
            if value is None:
                continue
            observations.append(_obs(universe, isin, field, value, "INTERNAL_FROM_OHLCV", "C"))
        if indicators.get("perf_1y_pct") is not None:
            relative_strength = round(indicators["perf_1y_pct"] - median_perf, 3)
            observations.append(_obs(universe, isin, "relative_strength", relative_strength, "INTERNAL_FROM_OHLCV", "C"))
    return observations


# ---------------------------------------------------------------- WAVE 04/05 helpers
def _priority_actions(actions_df: pd.DataFrame, top_n: int = 300) -> pd.DataFrame:
    priority_col = "comite_status"
    if priority_col in actions_df.columns:
        priority_df = actions_df[actions_df[priority_col].isin(["COMMITTEE", "WATCH"])]
    else:
        priority_df = pd.DataFrame(columns=actions_df.columns)
    if priority_df.empty and "score_brut" in actions_df.columns:
        scored = actions_df.copy()
        scored["_score"] = pd.to_numeric(scored["score_brut"], errors="coerce")
        priority_df = scored.sort_values("_score", ascending=False).head(top_n)
    return priority_df.copy()


def _row_has_any(row: pd.Series, fields: list[str]) -> bool:
    return any(field in row.index and not is_missing(row.get(field)) for field in fields)


def fundamentals_availability(actions_df: pd.DataFrame, top_n: int = 300) -> tuple[int, int, float]:
    priority_df = _priority_actions(actions_df, top_n)
    fields = ["per_ttm_yf", "per_forward_yf", "pb", "roe_api", "roa", "debt_to_equity", "free_cash_flow", "marge_ebit", "marge_nette"]
    available = sum(_row_has_any(row, fields) for _, row in priority_df.iterrows())
    total = len(priority_df)
    pct = 100.0 if total == 0 else round(available / total * 100, 2)
    return available, total, pct


def consensus_availability(actions_df: pd.DataFrame, top_n: int = 300) -> tuple[int, int, float]:
    priority_df = _priority_actions(actions_df, top_n)
    fields = ["consensus_rating", "consensus_score", "n_analysts", "target_price", "recommendation_key_yf", "recommendation_mean_yf", "target_mean_yf"]
    available = sum(_row_has_any(row, fields) for _, row in priority_df.iterrows())
    total = len(priority_df)
    pct = 100.0 if total == 0 else round(available / total * 100, 2)
    return available, total, pct


# ---------------------------------------------------------------- WAVE 04
def wave4_info_actions(actions_df: pd.DataFrame, cfg: dict, top_n: int = 300) -> tuple[list[dict], list[dict], dict]:
    """Wave 04: yfinance fundamentals on priority Actions, with no-repeat logic."""
    import time
    priority_df = _priority_actions(actions_df, top_n)
    available_before, total, pct_before = fundamentals_availability(actions_df, top_n)
    if "yf_status" in priority_df.columns:
        needs_refresh = priority_df[priority_df["yf_status"].fillna("").str.upper().ne("OK")]
    else:
        needs_refresh = priority_df

    ticker_to_isin = dict(zip(needs_refresh["yahoo_ticker"], needs_refresh["isin"]))
    tickers = [t for t in ticker_to_isin if not is_missing(t)]
    yf_cfg = cfg.get("yfinance", {})
    initial_cooldown = float(yf_cfg.get("info_initial_cooldown_seconds", 0) or 0)
    if tickers and initial_cooldown:
        time.sleep(initial_cooldown)

    observations, failures = collect_info(
        tickers,
        delay_seconds=float(yf_cfg.get("info_delay_seconds", 0.8) or 0),
        max_retries=int(yf_cfg.get("info_max_retries", 2) or 0),
        rate_limit_backoff_seconds=float(yf_cfg.get("info_rate_limit_backoff_seconds", 20) or 0),
        max_consecutive_rate_limits=int(yf_cfg.get("info_max_consecutive_rate_limits", 3) or 1),
    )

    result: list[dict] = []
    refreshed = set()
    for row in observations:
        isin = ticker_to_isin.get(row["ticker"])
        if isin is None:
            continue
        refreshed.add(row["ticker"])
        result.append(_obs("ACTION", isin, row["field"], row["value"], row.get("source", "yfinance"), "C"))
        result.append(_obs("ACTION", isin, "yf_status", "OK", row.get("source", "yfinance"), "C"))

    meta = {"priority": total, "available_before": available_before, "available_before_pct": pct_before,
            "attempted": len(tickers), "refreshed_tickers": len(refreshed), "failures": len(failures)}
    return result, failures, meta


# ---------------------------------------------------------------- WAVE 05
def _rating_from_yf(row: pd.Series) -> tuple[str | None, float | None]:
    key = str(row.get("recommendation_key_yf") or "").strip().lower().replace("-", "_")
    key_map = {"strong_buy": ("STRONG_BUY", 5.0), "buy": ("BUY", 4.0), "hold": ("HOLD", 3.0),
               "underperform": ("SELL", 2.0), "sell": ("SELL", 2.0), "strong_sell": ("STRONG_SELL", 1.0)}
    if key in key_map:
        return key_map[key]
    try:
        mean = float(row.get("recommendation_mean_yf"))
        score = max(1.0, min(5.0, round(6.0 - mean, 2)))
        if score >= 4.5:
            rating = "STRONG_BUY"
        elif score >= 3.5:
            rating = "BUY"
        elif score >= 2.5:
            rating = "HOLD"
        elif score >= 1.5:
            rating = "SELL"
        else:
            rating = "STRONG_SELL"
        return rating, score
    except (TypeError, ValueError):
        return None, None


def wave5_consensus_finnhub(actions_df: pd.DataFrame, api_key: str, top_n: int = 300,
                            symbol_cache_path: str | Path | None = None, cfg: dict | None = None) -> tuple[list[dict], list[dict], dict]:
    """Wave 05: canonical consensus from existing Yahoo data + Finnhub refresh."""
    from v182.sources.finnhub_consensus import fetch_consensus
    priority_df = _priority_actions(actions_df, top_n)
    available_before, total, pct_before = consensus_availability(actions_df, top_n)
    result: list[dict] = []
    covered_isins: set[str] = set()

    for _, row in priority_df.iterrows():
        isin = row.get("isin")
        rating, score = _rating_from_yf(row)
        if rating is None:
            continue
        covered_isins.add(isin)
        canonical = {"consensus": rating, "consensus_rating": rating, "consensus_score": score,
                     "consensus_status": "OK_EXISTING_YF", "consensus_source": "yfinance"}
        for source_field, target_field in (("n_analysts_yf", "n_analysts"), ("target_mean_yf", "target_price")):
            value = row.get(source_field)
            if not is_missing(value):
                canonical[target_field] = value
        for field, value in canonical.items():
            if value is not None:
                result.append(_obs("ACTION", isin, field, value, "yfinance", "C"))

    unresolved = priority_df[~priority_df["isin"].isin(covered_isins)]
    securities = unresolved[[c for c in ["isin", "name", "yahoo_ticker"] if c in unresolved.columns]].to_dict("records")
    fcfg = (cfg or {}).get("finnhub", {})
    obs_raw, failures = fetch_consensus(securities, api_key, symbol_cache_path=symbol_cache_path,
                                        delay_seconds=float(fcfg.get("delay_seconds", 1.05) or 0),
                                        max_retries=int(fcfg.get("max_retries", 2) or 0))

    ticker_to_isin = {t: i for t, i in zip(unresolved["yahoo_ticker"], unresolved["isin"]) if not is_missing(t)}
    finnhub_tickers = set()
    for row in obs_raw:
        isin = row.get("isin") or ticker_to_isin.get(row.get("ticker"))
        if isin is None:
            continue
        finnhub_tickers.add(row.get("ticker"))
        result.append(_obs("ACTION", isin, row["field"], row["value"], "Finnhub", "B"))

    meta = {"priority": total, "available_before": available_before, "available_before_pct": pct_before,
            "normalized_yf_tickers": len(covered_isins), "attempted_finnhub": len(securities),
            "finnhub_success_tickers": len({x for x in finnhub_tickers if x}), "failures": len(failures)}
    return result, failures, meta


# ---------------------------------------------------------------- WAVE 06
def wave6_etf_info(etf_with_tickers, cfg: dict) -> tuple[list[dict], list[dict]]:
    valid = etf_with_tickers[etf_with_tickers["yahoo_ticker"].apply(lambda v: not is_missing(v))]
    ticker_to_isin = dict(zip(valid["yahoo_ticker"], valid["isin"]))
    obs_raw, failures = collect_info(list(ticker_to_isin), delay_seconds=cfg["yfinance"].get("info_delay_seconds", 0.4))
    result = []
    for row in obs_raw:
        isin = ticker_to_isin.get(row["ticker"])
        if isin is None or row["field"] not in {"dividend_yield_pct"}:
            continue
        result.append(_obs("ETF", isin, row["field"], row["value"], "yfinance", "C"))
        result.append(_obs("ETF", isin, "dividend_data_status", "OK", "yfinance", "C"))
    return result, failures


# ---------------------------------------------------- WAVE 05/06 (repli scraping)
def wave_public_table(rows: pd.DataFrame, universe: str, field_map: dict[str, str], url_template: str,
                      selectors: dict[str, str], source_name: str, evidence: str,
                      delay_seconds: float = 0.6) -> tuple[list[dict], list[dict]]:
    import time
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return [], [{"reason": "MISSING_DEPENDENCY", "source": source_name}]

    observations: list[dict] = []
    failures: list[dict] = []
    headers = {"User-Agent": "V18.2-Completeness/1.0"}
    for _, row in rows.iterrows():
        symbol = row.get("euronext_symbol") or row.get("yahoo_ticker")
        isin = row.get("isin")
        if is_missing(symbol) or is_missing(isin):
            failures.append({"isin": isin, "reason": "NO_SYMBOL", "source": source_name})
            continue
        url = url_template.format(symbol=symbol, isin=isin)
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            for field, css_selector in selectors.items():
                node = soup.select_one(css_selector)
                if node is None:
                    continue
                target_field = field_map.get(field, field)
                observations.append(_obs(universe, isin, target_field, node.get_text(strip=True), source_name, evidence))
        except Exception as exc:
            failures.append({"isin": isin, "reason": type(exc).__name__, "source": source_name})
        time.sleep(delay_seconds)
    return observations, failures


# ---------------------------------------------------------------- WAVE 07
def wave7_official_validation(quarantine: list[dict], overrides_path: str | Path) -> list[dict]:
    overrides_file = Path(overrides_path)
    if not overrides_file.exists():
        return []
    overrides = pd.read_csv(overrides_file, sep=";", encoding="utf-8-sig", dtype=str)
    resolved: list[dict] = []
    for _, override in overrides.iterrows():
        match = [q for q in quarantine if q["isin"] == override["isin"] and q["field"] == override["field"]]
        if not match:
            continue
        resolved.append(_obs(match[0]["universe"], override["isin"], override["field"], override["value"], "Issuer/AMF/Euronext", "A"))
    return resolved


# ---------------------------------------------------------------- WAVE 08
def wave8_scenarios(actions_df: pd.DataFrame, shortlist_isins: set[str]) -> list[dict]:
    observations: list[dict] = []
    subset = actions_df[actions_df["isin"].isin(shortlist_isins)]
    for _, row in subset.iterrows():
        isin = row["isin"]
        try:
            last_close = float(row.get("last_close"))
            atr14 = float(row.get("atr14"))
        except (TypeError, ValueError):
            continue
        if last_close <= 0:
            continue
        amplitude = atr14 / last_close
        bull = round(3 * amplitude * 100, 2)
        bear = round(-3 * amplitude * 100, 2)
        try:
            perf_3m = float(row.get("perf_3m_pct"))
            base = round(perf_3m / 2, 2)
        except (TypeError, ValueError):
            base = 0.0
        for field, value in {"scenario_bull_pct": bull, "scenario_bear_pct": bear, "scenario_base_pct": base,
                             "asymmetry": round(bull + bear, 2), "invalidation_level": round(last_close - 2 * atr14, 4)}.items():
            observations.append(_obs("ACTION", isin, field, value, "INTERNAL_SHORTLIST_ENGINE", "C"))
    return observations
