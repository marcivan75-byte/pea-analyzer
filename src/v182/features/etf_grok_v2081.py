from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import math
import re
from typing import Mapping

import numpy as np
import pandas as pd

from v182.decision.etf_grok_high_precision import (
    Candidate,
    MarketRegime,
    momo_risk_on,
    select_candidates,
    weighted_raw_score,
)

MIN_HISTORY_SESSIONS = 757
MAX_STALENESS_CALENDAR_DAYS = 7
MARKET_PROXY_ID = "PEA_ETF_GROK_EQUAL_WEIGHT_PROXY"


def _safe_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _series(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(dtype=float)
    values = pd.to_numeric(frame[name], errors="coerce")
    return values.dropna().astype(float)


def _perf(close: pd.Series, sessions: int) -> float:
    if len(close) <= sessions:
        return np.nan
    return float(close.iloc[-1] / close.iloc[-sessions - 1] - 1.0)


def _rsi(close: pd.Series, window: int = 14) -> float:
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(window).mean()
    losses = -delta.clip(upper=0).rolling(window).mean()
    rs = gains / losses.replace(0, np.nan)
    value = 100.0 - 100.0 / (1.0 + rs)
    return float(value.iloc[-1]) if len(value) and pd.notna(value.iloc[-1]) else np.nan


def _max_drawdown(close: pd.Series, sessions: int) -> float:
    window = close.tail(sessions)
    if len(window) < 2:
        return np.nan
    dd = window / window.cummax() - 1.0
    return float(dd.min())


def _aligned_returns(close: pd.Series, proxy_close: pd.Series, sessions: int = 252) -> pd.DataFrame:
    joined = pd.concat([close.rename("etf"), proxy_close.rename("market")], axis=1, join="inner").dropna()
    return joined.pct_change(fill_method=None).dropna().tail(sessions)


def compute_raw_features(frame: pd.DataFrame, market_proxy_close: pd.Series) -> dict[str, float]:
    """Compute the cloned 38 PIT features from daily OHLCV data."""
    frame = frame.sort_index()
    close = _series(frame, "Close")
    volume = _series(frame, "Volume")
    if len(close) < MIN_HISTORY_SESSIONS or len(volume) < 126:
        return {}

    returns = close.pct_change(fill_method=None)
    sma50 = close.rolling(50).mean()
    sma100 = close.rolling(100).mean()
    sma200 = close.rolling(200).mean()
    perf_3m = _perf(close, 63)
    previous_3m = float(close.iloc[-64] / close.iloc[-127] - 1.0) if len(close) >= 127 else np.nan
    rsi14 = _rsi(close)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()

    r20 = returns.tail(20).dropna()
    r60 = returns.tail(60).dropna()
    r126 = returns.tail(126).dropna()
    r252 = returns.tail(252).dropna()
    negative60 = r60[r60 < 0]
    negative126 = r126[r126 < 0]
    path63 = close.tail(64).diff().abs().sum()
    efficiency63 = abs(float(close.iloc[-1] - close.iloc[-64])) / float(path63) if len(close) >= 64 and path63 > 0 else np.nan
    maxdd6 = _max_drawdown(close, 126)
    maxdd1 = _max_drawdown(close, 252)
    current_dd1 = float(close.iloc[-1] / close.tail(252).max() - 1.0)
    ann_vol20 = float(r20.std(ddof=1) * np.sqrt(252)) if len(r20) >= 2 else np.nan
    ann_vol60 = float(r60.std(ddof=1) * np.sqrt(252)) if len(r60) >= 2 else np.nan
    ann_vol1y = float(r252.std(ddof=1) * np.sqrt(252)) if len(r252) >= 2 else np.nan
    downside_vol60 = float(negative60.std(ddof=1) * np.sqrt(252)) if len(negative60) >= 2 else np.nan
    downside_vol126 = float(np.sqrt((negative126.pow(2)).mean()) * np.sqrt(252)) if len(negative126) else np.nan
    r126_std = r126.std(ddof=1) if len(r126) >= 2 else np.nan
    sharpe126 = float(r126.mean() / r126_std * np.sqrt(252)) if pd.notna(r126_std) and r126_std > 0 else np.nan
    sortino126 = float(r126.mean() * 252 / downside_vol126) if pd.notna(downside_vol126) and downside_vol126 > 0 else np.nan
    perf1y = _perf(close, 252)
    calmar252 = float(perf1y / abs(maxdd1)) if pd.notna(maxdd1) and maxdd1 < 0 else np.nan
    gains = r126[r126 > 0].sum()
    losses = abs(r126[r126 < 0].sum())
    gain_to_pain = float(gains / losses) if losses > 0 else np.nan
    mean_notional20 = (close * volume).tail(20).replace([np.inf, -np.inf], np.nan).dropna().mean()
    notional_volume20 = float(np.log(mean_notional20)) if pd.notna(mean_notional20) and mean_notional20 > 0 else np.nan
    avg_vol20 = volume.tail(20).mean()
    avg_vol126 = volume.tail(126).mean()
    proxy = market_proxy_close.sort_index().dropna()
    proxy_perf6 = _perf(proxy, 126)
    proxy_perf1y = _perf(proxy, 252)
    aligned = _aligned_returns(close, proxy, 252)
    beta252 = np.nan
    corr252 = np.nan
    if len(aligned) >= 126:
        var_market = aligned["market"].var(ddof=1)
        if pd.notna(var_market) and var_market > 0:
            beta252 = float(aligned["etf"].cov(aligned["market"]) / var_market)
        corr = aligned["etf"].corr(aligned["market"])
        if pd.notna(corr):
            corr252 = float(corr)
    trend_alignment = (
        int(close.iloc[-1] > sma50.iloc[-1])
        + int(sma50.iloc[-1] > sma100.iloc[-1])
        + int(sma100.iloc[-1] > sma200.iloc[-1])
    ) / 3.0

    values = {
        "perf_1m": _perf(close, 21), "perf_2m": _perf(close, 42), "perf_3m": perf_3m,
        "momentum_accel": perf_3m - previous_3m, "positive_days_63": float((returns.tail(63) > 0).mean()),
        "perf_6m": _perf(close, 126), "perf_9m": _perf(close, 189), "perf_1y": perf1y, "perf_3y": _perf(close, 756),
        "relative_strength_6m": _perf(close, 126) - proxy_perf6, "relative_strength_1y": perf1y - proxy_perf1y,
        "dist_sma50": float(close.iloc[-1] / sma50.iloc[-1] - 1.0), "dist_sma100": float(close.iloc[-1] / sma100.iloc[-1] - 1.0),
        "dist_sma200": float(close.iloc[-1] / sma200.iloc[-1] - 1.0), "slope_sma50": float(sma50.iloc[-1] / sma50.iloc[-22] - 1.0),
        "slope_sma200": float(sma200.iloc[-1] / sma200.iloc[-64] - 1.0), "trend_alignment": float(trend_alignment),
        "efficiency_63": float(efficiency63), "rsi_quality": float(rsi14), "macd_norm": float(macd.iloc[-1] / close.iloc[-1]),
        "macd_hist_norm": float((macd.iloc[-1] - signal.iloc[-1]) / close.iloc[-1]), "vol20": ann_vol20, "vol60": ann_vol60,
        "vol_1y": ann_vol1y, "downside_vol60": downside_vol60, "maxdd_6m": maxdd6, "maxdd_1y": maxdd1,
        "current_dd_1y": current_dd1, "tail_loss_126": float(r126.quantile(0.05)) if len(r126) else np.nan,
        "sharpe_126": sharpe126, "sortino_126": sortino126, "calmar_252": calmar252, "gain_to_pain_126": gain_to_pain,
        "notional_volume20": notional_volume20, "rvol20": float(volume.iloc[-1] / avg_vol20) if avg_vol20 > 0 else np.nan,
        "volume_trend": float(avg_vol20 / avg_vol126 - 1.0) if avg_vol126 > 0 else np.nan, "beta252": beta252, "corr_market252": corr252,
    }
    return {name: float(value) for name, value in values.items() if pd.notna(value) and math.isfinite(float(value))}


def build_equal_weight_market_proxy(histories: Mapping[str, pd.DataFrame]) -> pd.Series:
    closes: list[pd.Series] = []
    for instrument_id, frame in histories.items():
        close = _series(frame.sort_index(), "Close")
        if len(close) >= 200:
            closes.append(close.rename(instrument_id))
    if not closes:
        return pd.Series(dtype=float, name=MARKET_PROXY_ID)
    panel = pd.concat(closes, axis=1, join="outer").sort_index()
    proxy_returns = panel.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0.0)
    proxy = (1.0 + proxy_returns).cumprod()
    proxy.name = MARKET_PROXY_ID
    return proxy


def _criterion_scores(raw: pd.DataFrame, criteria_cfg: Mapping[str, dict]) -> pd.DataFrame:
    scores = pd.DataFrame(index=raw.index)
    for name, spec in criteria_cfg.items():
        values = pd.to_numeric(raw[name], errors="coerce")
        direction = spec["direction"]
        if direction == "HIGH":
            scores[name] = values.rank(method="average", pct=True) * 100.0
        elif direction == "LOW":
            scores[name] = values.rank(method="average", pct=True, ascending=False) * 100.0
        elif direction == "NONLINEAR":
            optimum = float(spec.get("optimum", 58.0))
            scores[name] = (-(values - optimum).abs()).rank(method="average", pct=True) * 100.0
        else:
            raise ValueError(f"unsupported criterion direction: {name}={direction}")
    return scores


def _exposure_group(row: pd.Series) -> str:
    for field in ("official_benchmark", "category", "geo_exposure", "morningstar_category"):
        value = row.get(field)
        if value is not None and not pd.isna(value) and str(value).strip():
            text = re.sub(r"[^A-Z0-9]+", "_", str(value).upper()).strip("_")
            if text:
                return text[:80]
    return "UNCLASSIFIED"


def market_regime(raw: pd.DataFrame, market_proxy_close: pd.Series) -> tuple[MarketRegime, dict]:
    valid = raw.dropna(subset=["dist_sma50", "dist_sma200", "perf_1m", "perf_6m"])
    if valid.empty:
        regime = MarketRegime(0.0, -1.0, -1.0, False)
        return regime, {"breadth50": 0.0, "breadth200": 0.0, "median_perf_1m": None, "median_perf_6m": None, "median_vol20": None, "market_trend200": None, "market_proxy": MARKET_PROXY_ID}
    breadth50 = float((valid["dist_sma50"] > 0).mean())
    breadth200 = float((valid["dist_sma200"] > 0).mean())
    median_perf1 = float(valid["perf_1m"].median())
    median_perf6 = float(valid["perf_6m"].median())
    median_vol20 = float(valid["vol20"].median())
    proxy = market_proxy_close.dropna()
    sma200 = proxy.rolling(200).mean()
    trend200 = float(proxy.iloc[-1] / sma200.iloc[-1] - 1.0) if len(proxy) >= 200 and pd.notna(sma200.iloc[-1]) else np.nan
    regime = MarketRegime(breadth200, median_perf1, median_perf6, bool(pd.notna(trend200) and trend200 > 0))
    return regime, {"breadth50": breadth50, "breadth200": breadth200, "median_perf_1m": median_perf1, "median_perf_6m": median_perf6, "median_vol20": _safe_float(median_vol20), "market_trend200": _safe_float(trend200), "market_proxy": MARKET_PROXY_ID}


def score_snapshot(histories: Mapping[str, pd.DataFrame], etf_reference: pd.DataFrame, grok_config: Mapping) -> tuple[pd.DataFrame, dict]:
    criteria_cfg = grok_config["dynamic_criteria"]
    expected = list(criteria_cfg)
    if len(expected) != 38:
        raise ValueError(f"ETF GROK requires exactly 38 dynamic criteria, got {len(expected)}")
    proxy = build_equal_weight_market_proxy(histories)
    if proxy.empty:
        raise ValueError("cannot build ETF GROK market proxy from OHLCV histories")
    global_latest = max((frame.index.max() for frame in histories.values() if not frame.empty), default=None)
    rows: list[dict] = []
    for instrument_id, frame in histories.items():
        if frame.empty:
            continue
        features = compute_raw_features(frame, proxy)
        latest = frame.index.max()
        stale_days = int((pd.Timestamp(global_latest).normalize() - pd.Timestamp(latest).normalize()).days) if global_latest is not None else 999
        row = {"instrument_id": instrument_id, "feature_as_of": str(pd.Timestamp(latest).date()), "history_sessions": int(len(frame)), "staleness_days": stale_days, **{name: features.get(name, np.nan) for name in expected}}
        missing = [name for name in expected if pd.isna(row[name])]
        if stale_days > MAX_STALENESS_CALENDAR_DAYS:
            missing.append("STALE_OHLCV")
        row["missing_criteria_count"] = len(missing)
        row["missing_criteria"] = "|".join(missing)
        row["criteria_complete"] = len(missing) == 0
        rows.append(row)
    snapshot = pd.DataFrame(rows)
    if snapshot.empty:
        raise ValueError("no ETF histories available for ETF GROK scoring")
    ref = etf_reference.copy()
    if "isin" not in ref.columns:
        raise ValueError("ETF reference must contain isin")
    ref = ref.drop_duplicates("isin").set_index("isin", drop=False)
    for col in ("score_raw", "score_rank_pct", "score_final", "rank_on_date"):
        snapshot[col] = np.nan
    complete = snapshot[snapshot["criteria_complete"]].copy()
    if not complete.empty:
        raw = complete.set_index("instrument_id")[expected]
        criterion_scores = _criterion_scores(raw, criteria_cfg)
        weights = {name: float(spec["backtested_weight"]) for name, spec in criteria_cfg.items()}
        raw_scores = criterion_scores.apply(lambda row: weighted_raw_score(row.to_dict(), weights), axis=1)
        rank_scores = raw_scores.rank(method="average", pct=True) * 100.0
        final_scores = float(grok_config["score"]["score_raw_weight"]) * raw_scores + float(grok_config["score"]["cross_section_rank_weight"]) * rank_scores
        ranking = final_scores.rank(method="min", ascending=False).astype(int)
        for instrument_id in raw_scores.index:
            mask = snapshot["instrument_id"] == instrument_id
            snapshot.loc[mask, "score_raw"] = float(raw_scores.loc[instrument_id])
            snapshot.loc[mask, "score_rank_pct"] = float(rank_scores.loc[instrument_id])
            snapshot.loc[mask, "score_final"] = float(final_scores.loc[instrument_id])
            snapshot.loc[mask, "rank_on_date"] = int(ranking.loc[instrument_id])
    regime_raw = snapshot[snapshot["criteria_complete"]].set_index("instrument_id")[expected].copy()
    regime, regime_metrics = market_regime(regime_raw, proxy)
    regime_allowed = momo_risk_on(regime)
    snapshot["regime_allowed"] = regime_allowed
    candidates: list[Candidate] = []
    if regime_allowed:
        for _, row in snapshot[snapshot["criteria_complete"]].iterrows():
            instrument_id = str(row["instrument_id"])
            reference_row = ref.loc[instrument_id] if instrument_id in ref.index else pd.Series(dtype=object)
            if isinstance(reference_row, pd.DataFrame):
                reference_row = reference_row.iloc[0]
            candidates.append(Candidate(instrument_id, float(row["score_raw"]), float(row["score_rank_pct"]), _exposure_group(reference_row)))
    selected = select_candidates(candidates, regime)
    selected_ids = {candidate.instrument_id for candidate in selected}
    snapshot["selected"] = snapshot["instrument_id"].isin(selected_ids)
    threshold = float(grok_config["score"]["selection_threshold"])
    snapshot["decision"] = "BLOCK_DATA"
    snapshot.loc[snapshot["criteria_complete"], "decision"] = "REJECT_SCORE"
    if not regime_allowed:
        snapshot.loc[snapshot["criteria_complete"], "decision"] = "ABSTAIN_REGIME"
    else:
        snapshot.loc[snapshot["criteria_complete"] & (snapshot["score_final"] >= threshold), "decision"] = "WATCH_NOT_TOP2"
        snapshot.loc[snapshot["selected"], "decision"] = "BUY_CANDIDATE"
    metadata_cols = [c for c in ("isin", "name", "provider", "category", "official_benchmark", "geo_exposure", "ticker_yahoo_final", "yahoo_ticker", "ter_pct", "aum_m", "fund_total_assets_eur_m", "dividend_yield_pct", "morningstar_rating", "risk_indicator") if c in etf_reference.columns]
    meta = etf_reference[metadata_cols].drop_duplicates("isin").rename(columns={"isin": "instrument_id"})
    snapshot = snapshot.merge(meta, on="instrument_id", how="left")
    snapshot = snapshot.sort_values(["selected", "score_final", "instrument_id"], ascending=[False, False, True], na_position="last").reset_index(drop=True)
    summary = {
        "version": grok_config.get("version", "20.8.1-grok"), "module": "ETF_GROK_HIGH_PRECISION",
        "as_of": str(pd.Timestamp(global_latest).date()) if global_latest is not None else None,
        "market_proxy": MARKET_PROXY_ID, "universe_histories": len(histories), "scorable_etfs": int(snapshot["criteria_complete"].sum()),
        "blocked_data_etfs": int((~snapshot["criteria_complete"]).sum()), "regime": {**asdict(regime), **regime_metrics, "allowed": regime_allowed},
        "selected": [{"isin": candidate.instrument_id, "score_final": candidate.score_final, "exposure_group": candidate.exposure_group} for candidate in selected],
        "selection_threshold": threshold, "top_n": int(grok_config["score"]["top_n"]),
        "target_win_rate": float(grok_config["objective"]["target_win_rate"]), "target_win_rate_guaranteed": bool(grok_config["objective"]["guaranteed"]),
        "historical_attribution": "CLONED_PARITY_ONLY_UNTIL_GROK_DIVERGES"
    }
    return snapshot, summary


def load_histories_from_cache(cache_dir: str | Path, ticker_to_isin: Mapping[str, str]) -> dict[str, pd.DataFrame]:
    histories: dict[str, pd.DataFrame] = {}
    for parquet_file in sorted(Path(cache_dir).glob("history_*.parquet")):
        frame = pd.read_parquet(parquet_file)
        if frame.empty:
            continue
        if isinstance(frame.columns, pd.MultiIndex):
            for ticker in frame.columns.get_level_values(0).unique():
                instrument_id = ticker_to_isin.get(str(ticker))
                if not instrument_id:
                    continue
                sub = frame[ticker].copy()
                if "Close" in sub.columns:
                    histories[instrument_id] = sub.sort_index()
        elif len(ticker_to_isin) == 1:
            _, instrument_id = next(iter(ticker_to_isin.items()))
            histories[instrument_id] = frame.sort_index()
    return histories


def write_outputs(snapshot: pd.DataFrame, summary: Mapping, output_dir: str | Path) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "V20.8.1_ETF_GROK_RANKING.csv"
    json_path = output / "V20.8.1_ETF_GROK_SUMMARY.json"
    snapshot.to_csv(csv_path, sep=";", index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ranking_csv": str(csv_path), "summary_json": str(json_path)}
