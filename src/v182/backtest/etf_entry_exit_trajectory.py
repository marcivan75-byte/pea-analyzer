from __future__ import annotations

from pathlib import Path
import json
import math
import numpy as np
import pandas as pd

from v182.backtest.exceptional_pit_oos import HOLDOUT_START, _naive_index, _read_csv, _read_json
from v182.features.etf_mt_v2081 import build_equal_weight_market_proxy, load_histories_from_cache, score_snapshot
from v182.sources.yfinance_bulk import download_history

ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_SESSIONS = 168
ENTRY_DELAYS = (1, 3, 5)
CHECKPOINTS = (5, 10, 21, 42, 63, 126, 168)


def _clean_close(history: pd.DataFrame) -> pd.Series:
    if "Close" not in history.columns:
        return pd.Series(dtype=float)
    close = pd.to_numeric(history["Close"], errors="coerce").dropna().sort_index()
    if close.empty:
        return close
    idx = _naive_index(close.index)
    close = pd.Series(close.to_numpy(dtype=float), index=idx).sort_index()
    return close[~close.index.duplicated(keep="last")]


def _path_metrics(close: pd.Series, signal_date: pd.Timestamp, entry_delay: int) -> dict | None:
    future = close[close.index > signal_date]
    if len(future) < entry_delay + 1:
        return None
    entry_idx = entry_delay - 1
    entry_date = pd.Timestamp(future.index[entry_idx])
    entry_price = float(future.iloc[entry_idx])
    if entry_price <= 0:
        return None
    path = future.iloc[entry_idx : entry_idx + ANALYSIS_SESSIONS]
    if path.empty or path.index[-1] >= HOLDOUT_START:
        return None

    returns = path / entry_price - 1.0
    running_peak = path.cummax()
    drawdown = path / running_peak - 1.0
    mfe = float(returns.max())
    mae = float(returns.min())
    mfe_date = pd.Timestamp(returns.idxmax())
    mae_date = pd.Timestamp(returns.idxmin())

    out = {
        "entry_delay_sessions": entry_delay,
        "entry_date": entry_date.date().isoformat(),
        "entry_price": entry_price,
        "sessions_observed": int(len(path)),
        "mfe": mfe,
        "mae": mae,
        "time_to_mfe_sessions": int(path.index.get_loc(mfe_date)) + 1,
        "time_to_mae_sessions": int(path.index.get_loc(mae_date)) + 1,
        "max_drawdown_from_running_peak": float(drawdown.min()),
        "final_return": float(returns.iloc[-1]),
    }
    for h in CHECKPOINTS:
        sub = returns.iloc[: min(h, len(returns))]
        if sub.empty:
            continue
        out[f"return_{h}s"] = float(sub.iloc[-1])
        out[f"mfe_{h}s"] = float(sub.max())
        out[f"mae_{h}s"] = float(sub.min())
    for level in (-0.03, -0.05, -0.07):
        hit = returns[returns <= level]
        out[f"hit_{int(abs(level)*100)}pct_loss"] = bool(not hit.empty)
        out[f"time_to_{int(abs(level)*100)}pct_loss"] = int(path.index.get_loc(hit.index[0])) + 1 if not hit.empty else None
        if not hit.empty:
            after = returns.loc[hit.index[0]:]
            out[f"recovered_after_{int(abs(level)*100)}pct_loss"] = bool((after >= 0.0).any())
        else:
            out[f"recovered_after_{int(abs(level)*100)}pct_loss"] = None
    return out


def _aggregate(paths: pd.DataFrame) -> pd.DataFrame:
    if paths.empty:
        return pd.DataFrame()
    rows = []
    for (period, delay), g in paths.groupby(["period", "entry_delay_sessions"], dropna=False):
        mfe = pd.to_numeric(g["mfe"], errors="coerce").dropna()
        mae = pd.to_numeric(g["mae"], errors="coerce").dropna()
        final = pd.to_numeric(g["final_return"], errors="coerce").dropna()
        rows.append({
            "period": period,
            "entry_delay_sessions": int(delay),
            "signals": int(len(g)),
            "median_mfe_pct": round(float(mfe.median()*100), 4) if not mfe.empty else None,
            "p25_mfe_pct": round(float(mfe.quantile(0.25)*100), 4) if not mfe.empty else None,
            "median_mae_pct": round(float(mae.median()*100), 4) if not mae.empty else None,
            "p25_mae_pct": round(float(mae.quantile(0.25)*100), 4) if not mae.empty else None,
            "median_final_return_pct": round(float(final.median()*100), 4) if not final.empty else None,
            "positive_final_rate": round(float((final > 0).mean()), 6) if not final.empty else None,
            "median_time_to_mfe_sessions": round(float(pd.to_numeric(g["time_to_mfe_sessions"], errors="coerce").median()), 2),
            "hit_3pct_loss_rate": round(float(g["hit_3pct_loss"].astype(bool).mean()), 6),
            "hit_5pct_loss_rate": round(float(g["hit_5pct_loss"].astype(bool).mean()), 6),
            "hit_7pct_loss_rate": round(float(g["hit_7pct_loss"].astype(bool).mean()), 6),
            "mfe_ge_10pct_rate": round(float((mfe >= 0.10).mean()), 6) if not mfe.empty else None,
            "mfe_ge_20pct_rate": round(float((mfe >= 0.20).mean()), 6) if not mfe.empty else None,
        })
    return pd.DataFrame(rows)


def _score_buckets(paths: pd.DataFrame) -> pd.DataFrame:
    if paths.empty:
        return pd.DataFrame()
    base = paths[paths["entry_delay_sessions"] == 1].copy()
    base["score_bucket"] = pd.cut(
        pd.to_numeric(base["score_final"], errors="coerce"),
        bins=[-np.inf, 84, 88, 92, np.inf],
        labels=["82-84", "84-88", "88-92", ">92"],
        right=False,
    )
    rows = []
    for (period, bucket), g in base.groupby(["period", "score_bucket"], observed=True):
        rows.append({
            "period": period,
            "score_bucket": str(bucket),
            "signals": int(len(g)),
            "median_mfe_pct": round(float(pd.to_numeric(g["mfe"], errors="coerce").median()*100), 4),
            "median_mae_pct": round(float(pd.to_numeric(g["mae"], errors="coerce").median()*100), 4),
            "median_final_return_pct": round(float(pd.to_numeric(g["final_return"], errors="coerce").median()*100), 4),
            "hit_7pct_loss_rate": round(float(g["hit_7pct_loss"].astype(bool).mean()), 6),
        })
    return pd.DataFrame(rows)


def run(root: Path = ROOT) -> dict:
    etf_path = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not etf_path.exists():
        etf_path = root / "inputs" / "V18.2_PEA_ETF_MASTER.csv"
    etfs = _read_csv(etf_path)
    if etfs.empty or "isin" not in etfs.columns:
        raise RuntimeError("ETF_INPUT_MISSING")
    ticker_col = "yahoo_ticker" if "yahoo_ticker" in etfs.columns else "ticker_yahoo_final" if "ticker_yahoo_final" in etfs.columns else None
    if ticker_col is None:
        raise RuntimeError("ETF_TICKER_MAPPING_MISSING")
    ticker_to_isin = {
        str(t).strip(): str(i).strip()
        for t, i in zip(etfs[ticker_col], etfs["isin"])
        if str(t).strip() and str(t).strip().lower() not in {"nan", "none", "<na>"}
    }

    cache = root / "data" / "cache" / "entry_exit_trajectory_etf"
    download = download_history(list(ticker_to_isin), str(cache), period="max", interval="1d", batch_size=30, auto_adjust=True, include_actions=False)
    histories = load_histories_from_cache(cache, ticker_to_isin)
    cfg = _read_json(root / "config" / "V20.8_ETF_MT_HIGH_PRECISION.json")
    proxy = build_equal_weight_market_proxy(histories)
    proxy_index = _naive_index(proxy.index)
    month_dates = pd.Series(proxy_index, index=proxy_index).groupby(proxy_index.to_period("M")).last().tolist()
    start_date = proxy_index[min(756, len(proxy_index)-1)]
    dates = [pd.Timestamp(d) for d in month_dates if pd.Timestamp(d) >= start_date and pd.Timestamp(d) < HOLDOUT_START]

    rows = []
    snapshot_failures = 0
    for signal_date in dates:
        sliced = {k: v.loc[:signal_date].copy() for k, v in histories.items() if not v.loc[:signal_date].empty}
        try:
            snapshot, _ = score_snapshot(sliced, etfs, cfg)
        except Exception:
            snapshot_failures += 1
            continue
        selected = snapshot[snapshot["selected"] == True]  # noqa: E712
        for _, r in selected.iterrows():
            isin = str(r["instrument_id"])
            close = _clean_close(histories.get(isin, pd.DataFrame()))
            if close.empty:
                continue
            period = "DEVELOPMENT" if signal_date.year <= 2020 else "VALIDATION_OOS" if signal_date.year <= 2023 else "DIAGNOSTIC_OOS"
            context = {
                "signal_date": signal_date.date().isoformat(),
                "period": period,
                "isin": isin,
                "score_final": float(r["score_final"]),
                "rank_on_date": int(r["rank_on_date"]),
            }
            for field in ("perf_1m", "perf_3m", "momentum_accel", "dist_sma50", "dist_sma200", "rsi_quality", "vol20", "current_dd_1y"):
                if field in r.index:
                    value = pd.to_numeric(pd.Series([r[field]]), errors="coerce").iloc[0]
                    context[field] = float(value) if pd.notna(value) and math.isfinite(float(value)) else None
            for delay in ENTRY_DELAYS:
                path = _path_metrics(close, signal_date, delay)
                if path is not None:
                    rows.append({**context, **path})

    paths = pd.DataFrame(rows)
    aggregate = _aggregate(paths)
    score_buckets = _score_buckets(paths)
    outdir = root / "outputs" / "research" / "etf_entry_exit_trajectory_2026_08_15"
    outdir.mkdir(parents=True, exist_ok=True)
    paths.to_csv(outdir / "ETF_SIGNAL_PATHS.csv", sep=";", index=False, encoding="utf-8-sig")
    aggregate.to_csv(outdir / "ETF_ENTRY_DELAY_AGGREGATES.csv", sep=";", index=False, encoding="utf-8-sig")
    score_buckets.to_csv(outdir / "ETF_SCORE_BUCKET_TRAJECTORIES.csv", sep=";", index=False, encoding="utf-8-sig")

    payload = {
        "status": "SUCCESS" if not paths.empty else "BLOCKED_NO_PATHS",
        "study": "ETF_ENTRY_EXIT_TRAJECTORY_NO_TAKE_PROFIT",
        "model_selection_source": "V20.8.1_EXACT_38_DYNAMIC_PIT_CORE",
        "take_profit_applied": False,
        "stop_loss_applied": False,
        "entry_delays_sessions": list(ENTRY_DELAYS),
        "analysis_sessions": ANALYSIS_SESSIONS,
        "holdout_opened": False,
        "weights_unchanged": True,
        "selection_threshold_unchanged": True,
        "snapshot_failures": snapshot_failures,
        "path_rows": int(len(paths)),
        "unique_signals": int(paths[["signal_date", "isin"]].drop_duplicates().shape[0]) if not paths.empty else 0,
        "download": {"requested": download.requested, "successful": len(download.successful), "failed": len(download.failed)},
        "purpose": "Observe post-signal MAE/MFE and entry-delay effects before designing future entry/hold/exit rules. No performance certification and no production-rule promotion.",
    }
    (outdir / "ETF_ENTRY_EXIT_TRAJECTORY_SUMMARY.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
