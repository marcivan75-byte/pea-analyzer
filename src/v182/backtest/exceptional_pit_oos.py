from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os
from typing import Mapping

import numpy as np
import pandas as pd

from v182.features.etf_mt_v2081 import (
    build_equal_weight_market_proxy,
    load_histories_from_cache,
    score_snapshot,
)
from v182.sources.yfinance_bulk import download_history

ROOT = Path(__file__).resolve().parents[3]
HOLDOUT_START = pd.Timestamp("2026-02-10")
HOLDOUT_CUTOFF = HOLDOUT_START - pd.Timedelta(days=1)
ACTION_OOS_START = pd.Timestamp("2023-01-01")
ROUND_TRIP_COST_BPS = 20.0


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    for sep in (";", ",", "\t"):
        try:
            frame = pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)
            if len(frame.columns) > 1:
                return frame
        except (OSError, UnicodeError, pd.errors.ParserError):
            continue
    return pd.DataFrame()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _naive_index(index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(pd.to_datetime(index, errors="coerce")).dropna()
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx


def _sector_from_row(row: pd.Series) -> str:
    for field in ("sector_yf", "sector_yahoo", "sector", "sector_bucket", "industry_yf", "industry"):
        value = row.get(field)
        text = str(value or "").strip()
        if text and text.lower() not in {"nan", "none", "n/a", "na", "unknown"}:
            return text
    return "NON_CLASSE"


def _load_action_histories(cache_dir: Path, ticker_to_isin: Mapping[str, str]) -> dict[str, pd.DataFrame]:
    histories: dict[str, pd.DataFrame] = {}
    for parquet_file in sorted(cache_dir.glob("history_*.parquet")):
        try:
            frame = pd.read_parquet(parquet_file)
        except Exception:
            continue
        if frame.empty:
            continue
        if isinstance(frame.columns, pd.MultiIndex):
            for ticker in frame.columns.get_level_values(0).unique():
                isin = ticker_to_isin.get(str(ticker))
                if not isin:
                    continue
                sub = frame[ticker].copy()
                if "Close" in sub.columns:
                    histories[isin] = sub.sort_index()
        elif len(ticker_to_isin) == 1:
            _, isin = next(iter(ticker_to_isin.items()))
            histories[isin] = frame.sort_index()
    return histories


def _wilson_lower(wins: int, n: int, z: float = 1.959963984540054) -> float | None:
    if n <= 0:
        return None
    p = wins / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (centre - margin) / denom


def _profit_factor(returns: pd.Series) -> float | None:
    values = pd.to_numeric(returns, errors="coerce").dropna()
    gains = float(values[values > 0].sum())
    losses = abs(float(values[values < 0].sum()))
    if losses <= 0:
        return None if gains <= 0 else float("inf")
    return gains / losses


def _period_label(signal_date: pd.Timestamp) -> str:
    if signal_date < ACTION_OOS_START:
        return "DEV_DIAGNOSTIC"
    return "OOS_DIAGNOSTIC"


def _action_monthly_frame(
    isin: str,
    sector: str,
    history: pd.DataFrame,
) -> pd.DataFrame:
    if "Close" not in history.columns:
        return pd.DataFrame()
    close = pd.to_numeric(history["Close"], errors="coerce").dropna().sort_index()
    if len(close) < 280:
        return pd.DataFrame()
    idx = _naive_index(close.index)
    close = pd.Series(close.to_numpy(dtype=float), index=idx).sort_index()
    close = close[~close.index.duplicated(keep="last")]
    data = pd.DataFrame(index=close.index)
    data["close"] = close
    data["high_52w"] = close.rolling(252, min_periods=252).max()
    data["distance_high_52w_pct"] = ((1.0 - close / data["high_52w"]) * 100.0).clip(lower=0.0)
    data["mm50"] = close.rolling(50, min_periods=50).mean()
    data["mm200"] = close.rolling(200, min_periods=200).mean()
    data["perf_1m_pct"] = (close / close.shift(21) - 1.0) * 100.0
    data["perf_3m_pct"] = (close / close.shift(63) - 1.0) * 100.0
    data["above_mm50"] = close > data["mm50"]
    data["above_mm200"] = close > data["mm200"]
    data["recovery"] = data["above_mm50"] & (data["perf_1m_pct"] > 0)

    distance = data["distance_high_52w_pct"]
    recovery = data["recovery"]
    data["high_52w_bonus_malus_points"] = np.select(
        [
            distance <= 2.0,
            distance <= 5.0,
            recovery & (distance >= 25.0),
            recovery & (distance >= 15.0),
            recovery & (distance >= 8.0),
        ],
        [-4.0, -2.0, 4.0, 2.5, 1.0],
        default=0.0,
    )
    raw = (distance / 25.0 * 100.0).clip(lower=0.0, upper=100.0)
    data["catchup_52w_score"] = np.where(recovery, raw, np.minimum(raw, 50.0))

    dates = pd.Series(data.index, index=data.index)
    entry = close.shift(-1)
    for horizon in (21, 63, 126):
        exit_close = close.shift(-(horizon + 1))
        data[f"future_return_{horizon}"] = exit_close / entry - 1.0
        data[f"exit_date_{horizon}"] = dates.shift(-(horizon + 1))

    data["signal_date"] = data.index
    data["month"] = data.index.to_period("M").astype(str)
    data["isin"] = isin
    data["sector"] = sector
    monthly = data.groupby("month", sort=True).tail(1).copy()
    return monthly.reset_index(drop=True)


def _add_sector_rotation(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows
    work = rows.copy()
    usable = work[(work["sector"] != "NON_CLASSE") & work["distance_high_52w_pct"].notna()].copy()
    if usable.empty:
        work["sector_rotation_score"] = np.nan
        work["action_catchup_score"] = work["catchup_52w_score"]
        work["market_high_regime_score"] = np.nan
        work["rotation_candidate_flag"] = False
        return work

    grouped = usable.groupby(["month", "sector"], dropna=False)
    sectors = grouped.agg(
        n_actions=("isin", "nunique"),
        median_distance_high_52w_pct=("distance_high_52w_pct", "median"),
        median_perf_1m_pct=("perf_1m_pct", "median"),
        median_perf_3m_pct=("perf_3m_pct", "median"),
        breadth_above_mm50_pct=("above_mm50", lambda s: float(pd.Series(s).dropna().mean() * 100.0) if pd.Series(s).dropna().size else np.nan),
        breadth_above_mm200_pct=("above_mm200", lambda s: float(pd.Series(s).dropna().mean() * 100.0) if pd.Series(s).dropna().size else np.nan),
    ).reset_index()
    sectors = sectors[sectors["n_actions"] >= 3].copy()
    if sectors.empty:
        work["sector_rotation_score"] = np.nan
        work["action_catchup_score"] = work["catchup_52w_score"]
        work["market_high_regime_score"] = np.nan
        work["rotation_candidate_flag"] = False
        return work

    sectors["momentum_acceleration"] = sectors["median_perf_1m_pct"] - sectors["median_perf_3m_pct"] / 3.0
    sectors["catchup_gap_score"] = (sectors["median_distance_high_52w_pct"] / 25.0 * 100.0).clip(0.0, 100.0)
    market_p1 = usable.groupby("month")["perf_1m_pct"].median().rename("market_median_perf_1m_pct").reset_index()
    sectors = sectors.merge(market_p1, on="month", how="left")
    sectors["rs_inflection"] = sectors["median_perf_1m_pct"] - sectors["market_median_perf_1m_pct"]
    sectors["momentum_rank"] = sectors.groupby("month")["median_perf_1m_pct"].rank(method="average", pct=True) * 100.0
    sectors["acceleration_rank"] = sectors.groupby("month")["momentum_acceleration"].rank(method="average", pct=True) * 100.0
    sectors["rs_rank"] = sectors.groupby("month")["rs_inflection"].rank(method="average", pct=True) * 100.0
    breadth = sectors[["breadth_above_mm50_pct", "breadth_above_mm200_pct"]].mean(axis=1, skipna=True)
    sectors["sector_rotation_score"] = (
        0.30 * sectors["catchup_gap_score"]
        + 0.25 * sectors["momentum_rank"]
        + 0.20 * sectors["acceleration_rank"]
        + 0.15 * breadth
        + 0.10 * sectors["rs_rank"]
    )
    sector_recovery = (sectors["median_perf_1m_pct"] > 0) & (
        (sectors["momentum_acceleration"] > 0) | (sectors["breadth_above_mm50_pct"] >= 50)
    )
    sectors.loc[~sector_recovery, "sector_rotation_score"] = sectors.loc[~sector_recovery, "sector_rotation_score"].clip(upper=50.0)
    sectors["sector_rotation_score"] = sectors["sector_rotation_score"].clip(0.0, 100.0)
    sectors["sector_recovery_gate"] = sector_recovery

    monthly = usable.groupby("month").agg(
        near_high_share_pct=("distance_high_52w_pct", lambda s: float((pd.to_numeric(s, errors="coerce") <= 5.0).mean() * 100.0)),
        breadth_above_mm200_pct_market=("above_mm200", lambda s: float(pd.Series(s).dropna().mean() * 100.0) if pd.Series(s).dropna().size else np.nan),
    ).reset_index()
    monthly["near_component"] = (monthly["near_high_share_pct"] / 40.0 * 100.0).clip(upper=100.0)
    monthly["market_high_regime_score"] = 0.60 * monthly["near_component"] + 0.40 * monthly["breadth_above_mm200_pct_market"]

    merge_cols = ["month", "sector", "sector_rotation_score", "sector_recovery_gate"]
    work = work.merge(sectors[merge_cols], on=["month", "sector"], how="left")
    work = work.merge(monthly[["month", "market_high_regime_score"]], on="month", how="left")
    catch = pd.to_numeric(work["catchup_52w_score"], errors="coerce")
    rotation = pd.to_numeric(work["sector_rotation_score"], errors="coerce")
    work["action_catchup_score"] = np.where(
        catch.notna() & rotation.notna(),
        0.55 * catch + 0.45 * rotation,
        np.where(catch.notna(), catch, rotation),
    )
    work["rotation_candidate_flag"] = (
        (pd.to_numeric(work["market_high_regime_score"], errors="coerce") >= 65.0)
        & (rotation >= 65.0)
        & (pd.to_numeric(work["action_catchup_score"], errors="coerce") >= 60.0)
    )
    return work


def _cohort_metrics(frame: pd.DataFrame, return_col: str, cohort: str, period: str, horizon: int) -> dict:
    values = pd.to_numeric(frame[return_col], errors="coerce").dropna()
    if values.empty:
        return {
            "period": period,
            "horizon_sessions": horizon,
            "cohort": cohort,
            "n": 0,
            "wins": 0,
            "win_rate": None,
            "wilson_95_lower": None,
            "mean_return_pct": None,
            "median_return_pct": None,
            "mean_excess_vs_month_pct": None,
            "beat_month_rate": None,
            "profit_factor": None,
        }
    wins = int((values > 0).sum())
    excess = pd.to_numeric(frame.loc[values.index, "excess_vs_month"], errors="coerce").dropna()
    beat = pd.to_numeric(frame.loc[values.index, "beat_month"], errors="coerce").dropna()
    pf = _profit_factor(values)
    return {
        "period": period,
        "horizon_sessions": horizon,
        "cohort": cohort,
        "n": int(len(values)),
        "wins": wins,
        "win_rate": round(wins / len(values), 6),
        "wilson_95_lower": round(float(_wilson_lower(wins, len(values))), 6),
        "mean_return_pct": round(float(values.mean() * 100.0), 4),
        "median_return_pct": round(float(values.median() * 100.0), 4),
        "mean_excess_vs_month_pct": round(float(excess.mean() * 100.0), 4) if not excess.empty else None,
        "beat_month_rate": round(float(beat.mean()), 6) if not beat.empty else None,
        "profit_factor": None if pf is None else ("INF" if math.isinf(pf) else round(float(pf), 6)),
    }


def action_overlay_backtest(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    actions_path = root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv"
    if not actions_path.exists():
        actions_path = root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv"
    actions = _read_csv(actions_path)
    if actions.empty or not {"isin", "yahoo_ticker"}.issubset(actions.columns):
        return pd.DataFrame(), pd.DataFrame(), {"status": "BLOCKED_ACTION_INPUT"}

    action_meta = actions.drop_duplicates("isin").set_index("isin", drop=False)
    ticker_to_isin = {
        str(ticker).strip(): str(isin).strip()
        for ticker, isin in zip(actions["yahoo_ticker"], actions["isin"])
        if str(ticker).strip() and str(ticker).strip().lower() not in {"nan", "none", "<na>"}
    }
    histories = _load_action_histories(root / "data" / "cache" / "actions", ticker_to_isin)
    rows: list[pd.DataFrame] = []
    for isin, history in histories.items():
        meta = action_meta.loc[isin] if isin in action_meta.index else pd.Series(dtype=object)
        if isinstance(meta, pd.DataFrame):
            meta = meta.iloc[0]
        monthly = _action_monthly_frame(isin, _sector_from_row(meta), history)
        if not monthly.empty:
            rows.append(monthly)
    observations = pd.concat(rows, ignore_index=True, sort=False) if rows else pd.DataFrame()
    if observations.empty:
        return observations, pd.DataFrame(), {"status": "BLOCKED_NO_ACTION_HISTORY"}

    observations = observations[observations["high_52w"].notna()].copy()
    observations = _add_sector_rotation(observations)
    observations["signal_date"] = pd.to_datetime(observations["signal_date"], errors="coerce")
    observations["period"] = observations["signal_date"].map(_period_label)

    metric_rows: list[dict] = []
    cohorts = {
        "ALL_ELIGIBLE": lambda f: pd.Series(True, index=f.index),
        "52W_BONUS": lambda f: pd.to_numeric(f["high_52w_bonus_malus_points"], errors="coerce") > 0,
        "52W_MALUS": lambda f: pd.to_numeric(f["high_52w_bonus_malus_points"], errors="coerce") < 0,
        "NEAR_HIGH_LE5": lambda f: pd.to_numeric(f["distance_high_52w_pct"], errors="coerce") <= 5.0,
        "ROTATION_CANDIDATE": lambda f: f["rotation_candidate_flag"].fillna(False).astype(bool),
        "ACTION_CATCHUP_GE70": lambda f: pd.to_numeric(f["action_catchup_score"], errors="coerce") >= 70.0,
    }
    for horizon in (21, 63, 126):
        return_col = f"future_return_{horizon}"
        exit_col = f"exit_date_{horizon}"
        eligible = observations.copy()
        eligible[exit_col] = pd.to_datetime(eligible[exit_col], errors="coerce")
        eligible = eligible[
            eligible[return_col].notna()
            & eligible[exit_col].notna()
            & (eligible[exit_col] < HOLDOUT_START)
        ].copy()
        if eligible.empty:
            continue
        eligible["month_benchmark"] = eligible.groupby("month")[return_col].transform("mean")
        eligible["excess_vs_month"] = eligible[return_col] - eligible["month_benchmark"]
        eligible["beat_month"] = (eligible[return_col] > eligible["month_benchmark"]).astype(float)
        for period in ("DEV_DIAGNOSTIC", "OOS_DIAGNOSTIC"):
            period_frame = eligible[eligible["period"] == period]
            for cohort, selector in cohorts.items():
                selected = period_frame.loc[selector(period_frame)].copy()
                metric_rows.append(_cohort_metrics(selected, return_col, cohort, period, horizon))

    metrics = pd.DataFrame(metric_rows)
    summary = {
        "status": "SUCCESS" if not metrics.empty else "BLOCKED_NO_ELIGIBLE_ACTION_OBSERVATIONS",
        "histories_loaded": int(len(histories)),
        "monthly_observations": int(len(observations)),
        "holdout_start": HOLDOUT_START.date().isoformat(),
        "holdout_opened": False,
        "pit_quality": {
            "52w_bonus_malus": "PIT_FROM_OHLCV_CURRENT_SURVIVOR_UNIVERSE",
            "sector_rotation": "PIT_MARKET_DATA_WITH_CURRENT_STATIC_SECTOR_MAPPING_AND_SURVIVOR_UNIVERSE",
        },
        "certification_eligible": False,
        "certification_blockers": [
            "current-survivor Action universe is not point-in-time",
            "sector classification is current/static rather than historically versioned",
            "V21.7 fundamentals/consensus are not reconstructed historically in this diagnostic",
        ],
    }
    return observations, metrics, summary


def _simulate_etf_trade(
    history: pd.DataFrame,
    signal_date: pd.Timestamp,
    target_return: float,
    stop_return: float,
    max_holding_sessions: int,
) -> dict | None:
    if "Close" not in history.columns:
        return None
    close = pd.to_numeric(history["Close"], errors="coerce").dropna().sort_index()
    idx = _naive_index(close.index)
    close = pd.Series(close.to_numpy(dtype=float), index=idx).sort_index()
    future = close[close.index > signal_date].head(max_holding_sessions)
    if len(future) < max_holding_sessions or future.index[-1] >= HOLDOUT_START:
        return None
    entry_date = future.index[0]
    entry = float(future.iloc[0])
    if entry <= 0:
        return None
    returns = future / entry - 1.0
    exit_reason = "MAX_HOLD"
    exit_date = future.index[-1]
    gross_return = float(returns.iloc[-1])
    for date, value in returns.iloc[1:].items():
        ret = float(value)
        if ret >= target_return:
            exit_reason = "TARGET"
            exit_date = pd.Timestamp(date)
            gross_return = ret
            break
        if ret <= stop_return:
            exit_reason = "STOP"
            exit_date = pd.Timestamp(date)
            gross_return = ret
            break
    net_return = gross_return - ROUND_TRIP_COST_BPS / 10000.0
    return {
        "entry_date": entry_date.date().isoformat(),
        "exit_date": exit_date.date().isoformat(),
        "exit_reason": exit_reason,
        "gross_return": gross_return,
        "net_return": net_return,
        "holding_sessions": int((future.index <= exit_date).sum()),
    }


def _trade_metrics(frame: pd.DataFrame, period: str) -> dict:
    if frame.empty:
        return {
            "period": period,
            "trades": 0,
            "wins": 0,
            "win_rate": None,
            "wilson_95_lower": None,
            "expectancy_net": None,
            "profit_factor_net": None,
        }
    returns = pd.to_numeric(frame["net_return"], errors="coerce").dropna()
    wins = int((returns > 0).sum())
    pf = _profit_factor(returns)
    return {
        "period": period,
        "trades": int(len(returns)),
        "wins": wins,
        "win_rate": round(wins / len(returns), 6) if len(returns) else None,
        "wilson_95_lower": round(float(_wilson_lower(wins, len(returns))), 6) if len(returns) else None,
        "expectancy_net": round(float(returns.mean()), 8) if len(returns) else None,
        "profit_factor_net": None if pf is None else ("INF" if math.isinf(pf) else round(float(pf), 6)),
    }


def etf_core_backtest(root: Path) -> tuple[pd.DataFrame, dict]:
    etf_path = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not etf_path.exists():
        etf_path = root / "inputs" / "V18.2_PEA_ETF_MASTER.csv"
    etfs = _read_csv(etf_path)
    if etfs.empty or "isin" not in etfs.columns:
        return pd.DataFrame(), {"status": "BLOCKED_ETF_INPUT"}
    ticker_col = "yahoo_ticker" if "yahoo_ticker" in etfs.columns else "ticker_yahoo_final" if "ticker_yahoo_final" in etfs.columns else None
    if ticker_col is None:
        return pd.DataFrame(), {"status": "BLOCKED_ETF_TICKERS"}
    ticker_to_isin = {
        str(ticker).strip(): str(isin).strip()
        for ticker, isin in zip(etfs[ticker_col], etfs["isin"])
        if str(ticker).strip() and str(ticker).strip().lower() not in {"nan", "none", "<na>"}
    }
    cache = root / "data" / "cache" / "exceptional_pit_oos_etf"
    source = "EXCEPTIONAL_MAX_HISTORY"
    download_summary: dict[str, object]
    try:
        result = download_history(
            list(ticker_to_isin),
            str(cache),
            period="max",
            interval="1d",
            batch_size=30,
            auto_adjust=True,
            include_actions=False,
        )
        download_summary = {
            "requested": result.requested,
            "successful": len(result.successful),
            "failed": len(result.failed),
        }
        histories = load_histories_from_cache(cache, ticker_to_isin)
    except Exception as exc:
        source = "FALLBACK_CURRENT_5Y_CACHE"
        download_summary = {"error": type(exc).__name__, "detail": str(exc)[:240]}
        histories = load_histories_from_cache(root / "data" / "cache" / "etf", ticker_to_isin)
    if not histories:
        return pd.DataFrame(), {"status": "BLOCKED_NO_ETF_HISTORY", "history_source": source, "download": download_summary}

    mt_config = _read_json(root / "config" / "V20.8_ETF_MT_HIGH_PRECISION.json")
    proxy = build_equal_weight_market_proxy(histories)
    if proxy.empty or len(proxy) < 760:
        return pd.DataFrame(), {"status": "BLOCKED_SHORT_ETF_HISTORY", "history_source": source, "download": download_summary}
    proxy_index = _naive_index(proxy.index)
    month_dates = pd.Series(proxy_index, index=proxy_index).groupby(proxy_index.to_period("M")).last().tolist()
    start_date = proxy_index[min(756, len(proxy_index) - 1)]
    dates = [pd.Timestamp(d) for d in month_dates if pd.Timestamp(d) >= start_date and pd.Timestamp(d) < HOLDOUT_START]

    exit_cfg = mt_config["exit_policy"]
    target_return = float(exit_cfg["target_return"])
    stop_return = float(exit_cfg["hard_stop_return"])
    max_hold = int(exit_cfg["max_holding_sessions"])
    trades: list[dict] = []
    snapshot_failures = 0
    scored_months = 0
    for signal_date in dates:
        sliced = {instrument_id: frame.loc[:signal_date].copy() for instrument_id, frame in histories.items() if not frame.loc[:signal_date].empty}
        try:
            snapshot, _summary = score_snapshot(sliced, etfs, mt_config)
        except Exception:
            snapshot_failures += 1
            continue
        scored_months += 1
        selected = snapshot[snapshot["selected"] == True]  # noqa: E712
        for _, row in selected.iterrows():
            instrument_id = str(row["instrument_id"])
            trade = _simulate_etf_trade(histories[instrument_id], signal_date, target_return, stop_return, max_hold)
            if trade is None:
                continue
            period = "DEVELOPMENT" if signal_date.year <= 2020 else "VALIDATION_OOS" if signal_date.year <= 2023 else "DIAGNOSTIC_OOS"
            trades.append({
                "signal_date": signal_date.date().isoformat(),
                "period": period,
                "isin": instrument_id,
                "score_final": float(row["score_final"]),
                "rank_on_date": int(row["rank_on_date"]),
                **trade,
            })
    trade_frame = pd.DataFrame(trades)
    metrics = [_trade_metrics(trade_frame[trade_frame["period"] == period] if not trade_frame.empty else pd.DataFrame(), period) for period in ("DEVELOPMENT", "VALIDATION_OOS", "DIAGNOSTIC_OOS")]
    summary = {
        "status": "SUCCESS" if not trade_frame.empty else "BLOCKED_NO_ELIGIBLE_ETF_TRADES",
        "model": "V20.8.1_EXACT_38_DYNAMIC_PIT_CORE",
        "history_source": source,
        "download": download_summary,
        "histories_loaded": int(len(histories)),
        "scored_months": int(scored_months),
        "snapshot_failures": int(snapshot_failures),
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "holdout_start": HOLDOUT_START.date().isoformat(),
        "holdout_opened": False,
        "metrics": metrics,
        "certification_eligible": False,
        "certification_blockers": [
            "current-survivor ETF universe is not point-in-time",
            "this exceptional rerun is diagnostic and may be used for subsequent corrections",
            "43-criterion 38+5 composite is not tested because structural PIT history is unavailable",
        ],
    }
    return trade_frame, summary


def run(root: Path = ROOT) -> dict:
    if os.environ.get("ALLOW_EXCEPTIONAL_PIT_OOS_ONCE") != "1":
        raise PermissionError("EXCEPTIONAL_PIT_OOS_DISABLED_USE_ONE_SHOT_FLAG")

    outdir = root / "outputs" / "backtest" / "exceptional_pit_oos_2026_08_14"
    outdir.mkdir(parents=True, exist_ok=True)

    action_observations, action_metrics, action_summary = action_overlay_backtest(root)
    etf_trades, etf_summary = etf_core_backtest(root)

    action_observations.to_csv(outdir / "ACTION_52W_ROTATION_PIT_OBSERVATIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    action_metrics.to_csv(outdir / "ACTION_52W_ROTATION_PIT_METRICS.csv", sep=";", index=False, encoding="utf-8-sig")
    etf_trades.to_csv(outdir / "ETF_MT_V20_8_1_PIT_TRADES.csv", sep=";", index=False, encoding="utf-8-sig")

    overall = "SUCCESS" if action_summary.get("status") == "SUCCESS" or etf_summary.get("status") == "SUCCESS" else "BLOCKED"
    payload = {
        "version": "EXCEPTIONAL_PIT_OOS_2026_08_14",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": overall,
        "one_shot": True,
        "holdout_policy": {
            "final_holdout_from": HOLDOUT_START.date().isoformat(),
            "final_holdout_opened": False,
            "all_labels_exit_before": HOLDOUT_START.date().isoformat(),
            "reason": "Preserve the final holdout because this run is followed by corrections and therefore cannot serve as certification.",
        },
        "actions_52w_rotation": action_summary,
        "etf_mt_38_core": etf_summary,
        "not_backtested_as_certified": {
            "actions_v21_7_full_model": "Historical PIT fundamentals/consensus are incomplete.",
            "etf_mt_43_composite": "Five structural criteria do not have complete historical PIT snapshots.",
            "boursorama_signals": "Current Boursorama observations are monitored but do not have sufficient historical PIT snapshots for OOS attribution.",
        },
        "governance": {
            "no_parameter_optimization_inside_backtest": True,
            "no_holdout_unlock": True,
            "no_real_orders": True,
            "results_are_diagnostic_not_certification": True,
        },
    }
    (outdir / "EXCEPTIONAL_PIT_OOS_SUMMARY.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
