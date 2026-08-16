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
BREACH_LEVELS = (-0.05, -0.07)
STATE_FEATURES = (
    "ret_5d",
    "ret_21d",
    "dist_sma50",
    "dist_sma200",
    "slope_sma50_20d",
    "drawdown_63d",
    "vol20_ann",
    "market_ret_21d",
    "market_dist_sma200",
)


def _clean_close(history: pd.DataFrame) -> pd.Series:
    if "Close" not in history.columns:
        return pd.Series(dtype=float)
    close = pd.to_numeric(history["Close"], errors="coerce").dropna().sort_index()
    if close.empty:
        return close
    idx = _naive_index(close.index)
    close = pd.Series(close.to_numpy(dtype=float), index=idx).sort_index()
    return close[~close.index.duplicated(keep="last")]


def _state(close: pd.Series, date: pd.Timestamp, market: pd.Series) -> dict[str, float | None]:
    hist = close.loc[:date]
    mh = market.loc[:date]
    if hist.empty:
        return {k: None for k in STATE_FEATURES}
    p = float(hist.iloc[-1])
    sma50 = float(hist.tail(50).mean()) if len(hist) >= 50 else np.nan
    sma200 = float(hist.tail(200).mean()) if len(hist) >= 200 else np.nan
    sma50_prev = float(hist.iloc[:-20].tail(50).mean()) if len(hist) >= 70 else np.nan
    ret5 = p / float(hist.iloc[-6]) - 1.0 if len(hist) >= 6 else np.nan
    ret21 = p / float(hist.iloc[-22]) - 1.0 if len(hist) >= 22 else np.nan
    slope50 = sma50 / sma50_prev - 1.0 if np.isfinite(sma50) and np.isfinite(sma50_prev) and sma50_prev else np.nan
    peak63 = float(hist.tail(63).max()) if len(hist) else np.nan
    dd63 = p / peak63 - 1.0 if np.isfinite(peak63) and peak63 else np.nan
    rets = hist.pct_change().dropna().tail(20)
    vol20 = float(rets.std(ddof=1) * math.sqrt(252)) if len(rets) >= 10 else np.nan

    market_p = float(mh.iloc[-1]) if not mh.empty else np.nan
    market_ret21 = market_p / float(mh.iloc[-22]) - 1.0 if len(mh) >= 22 else np.nan
    market_sma200 = float(mh.tail(200).mean()) if len(mh) >= 200 else np.nan
    market_dist200 = market_p / market_sma200 - 1.0 if np.isfinite(market_p) and np.isfinite(market_sma200) and market_sma200 else np.nan

    vals = {
        "ret_5d": ret5,
        "ret_21d": ret21,
        "dist_sma50": p / sma50 - 1.0 if np.isfinite(sma50) and sma50 else np.nan,
        "dist_sma200": p / sma200 - 1.0 if np.isfinite(sma200) and sma200 else np.nan,
        "slope_sma50_20d": slope50,
        "drawdown_63d": dd63,
        "vol20_ann": vol20,
        "market_ret_21d": market_ret21,
        "market_dist_sma200": market_dist200,
    }
    return {k: (float(v) if v is not None and np.isfinite(v) else None) for k, v in vals.items()}


def _breach_observation(close: pd.Series, market: pd.Series, signal_date: pd.Timestamp, level: float) -> dict | None:
    future = close[close.index > signal_date].head(ANALYSIS_SESSIONS)
    if len(future) < 2 or future.index[-1] >= HOLDOUT_START:
        return None
    entry_date = pd.Timestamp(future.index[0])
    entry = float(future.iloc[0])
    if entry <= 0:
        return None
    returns = future / entry - 1.0
    hit = returns[returns <= level]
    if hit.empty:
        return None
    breach_date = pd.Timestamp(hit.index[0])
    breach_return = float(hit.iloc[0])
    remaining = returns.loc[breach_date:]
    recovered = bool((remaining >= 0.0).any())
    recovery_date = pd.Timestamp(remaining[remaining >= 0.0].index[0]) if recovered else None
    worst_after = float(remaining.min())
    best_after = float(remaining.max())
    out = {
        "entry_date": entry_date.date().isoformat(),
        "entry_price": entry,
        "breach_level": level,
        "breach_date": breach_date.date().isoformat(),
        "breach_return": breach_return,
        "sessions_to_breach": int(future.index.get_loc(breach_date)) + 1,
        "recovered_to_entry": recovered,
        "recovery_date": recovery_date.date().isoformat() if recovery_date is not None else None,
        "sessions_breach_to_recovery": int(future.index.get_loc(recovery_date) - future.index.get_loc(breach_date)) if recovery_date is not None else None,
        "worst_return_after_breach": worst_after,
        "best_return_after_breach": best_after,
        "final_return_168": float(returns.iloc[-1]),
    }
    out.update(_state(close, breach_date, market))
    return out


def _group_summary(obs: pd.DataFrame) -> pd.DataFrame:
    if obs.empty:
        return pd.DataFrame()
    rows = []
    for (period, level, recovered), g in obs.groupby(["period", "breach_level", "recovered_to_entry"], dropna=False):
        row = {
            "period": period,
            "breach_level": float(level),
            "recovered_to_entry": bool(recovered),
            "n": int(len(g)),
            "median_sessions_to_breach": float(pd.to_numeric(g["sessions_to_breach"], errors="coerce").median()),
            "median_worst_after_pct": float(pd.to_numeric(g["worst_return_after_breach"], errors="coerce").median() * 100),
            "median_final_168_pct": float(pd.to_numeric(g["final_return_168"], errors="coerce").median() * 100),
        }
        for feature in STATE_FEATURES:
            s = pd.to_numeric(g[feature], errors="coerce").dropna()
            row[f"median_{feature}"] = float(s.median()) if not s.empty else None
        rows.append(row)
    return pd.DataFrame(rows)


def _development_median_rule_checks(obs: pd.DataFrame) -> pd.DataFrame:
    """Exploratory only: thresholds come from DEVELOPMENT medians, then are reported unchanged OOS."""
    rows = []
    for level in BREACH_LEVELS:
        dev = obs[(obs["period"] == "DEVELOPMENT") & (obs["breach_level"] == level)].copy()
        if dev.empty:
            continue
        for feature in STATE_FEATURES:
            x = pd.to_numeric(dev[feature], errors="coerce")
            threshold = float(x.median()) if x.notna().any() else np.nan
            if not np.isfinite(threshold):
                continue
            ge = dev[x >= threshold]
            lt = dev[x < threshold]
            ge_rate = float(ge["recovered_to_entry"].astype(bool).mean()) if len(ge) else np.nan
            lt_rate = float(lt["recovered_to_entry"].astype(bool).mean()) if len(lt) else np.nan
            fav = "GE" if (np.nan_to_num(ge_rate, nan=-1) >= np.nan_to_num(lt_rate, nan=-1)) else "LT"
            for period in ("DEVELOPMENT", "VALIDATION_OOS", "DIAGNOSTIC_OOS"):
                g = obs[(obs["period"] == period) & (obs["breach_level"] == level)].copy()
                gx = pd.to_numeric(g[feature], errors="coerce")
                mask = gx >= threshold if fav == "GE" else gx < threshold
                chosen = g[mask]
                other = g[~mask]
                rows.append({
                    "breach_level": level,
                    "feature": feature,
                    "development_threshold": threshold,
                    "development_favourable_side": fav,
                    "period": period,
                    "fav_n": int(len(chosen)),
                    "fav_recovery_rate": float(chosen["recovered_to_entry"].astype(bool).mean()) if len(chosen) else None,
                    "other_n": int(len(other)),
                    "other_recovery_rate": float(other["recovered_to_entry"].astype(bool).mean()) if len(other) else None,
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
    cache = root / "data" / "cache" / "breach_state_etf"
    dl = download_history(list(ticker_to_isin), str(cache), period="max", interval="1d", batch_size=30, auto_adjust=True, include_actions=False)
    histories = load_histories_from_cache(cache, ticker_to_isin)
    cfg = _read_json(root / "config" / "V20.8_ETF_MT_HIGH_PRECISION.json")
    proxy = build_equal_weight_market_proxy(histories)
    proxy_index = _naive_index(proxy.index)
    proxy = pd.Series(proxy.to_numpy(dtype=float), index=proxy_index).sort_index()
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
            for level in BREACH_LEVELS:
                obs = _breach_observation(close, proxy, signal_date, level)
                if obs is not None:
                    rows.append({
                        "signal_date": signal_date.date().isoformat(),
                        "period": period,
                        "isin": isin,
                        "score_final": float(r["score_final"]),
                        **obs,
                    })

    observations = pd.DataFrame(rows)
    summary = _group_summary(observations)
    checks = _development_median_rule_checks(observations)
    outdir = root / "outputs" / "research" / "etf_breach_state_2026_08_15"
    outdir.mkdir(parents=True, exist_ok=True)
    observations.to_csv(outdir / "ETF_BREACH_STATE_OBSERVATIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    summary.to_csv(outdir / "ETF_BREACH_STATE_GROUP_SUMMARY.csv", sep=";", index=False, encoding="utf-8-sig")
    checks.to_csv(outdir / "ETF_BREACH_STATE_DEV_RULE_OOS_CHECKS.csv", sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "status": "SUCCESS" if not observations.empty else "BLOCKED_NO_BREACH_OBSERVATIONS",
        "study": "ETF_BREACH_STATE_RECOVERY_NO_STOP_EXECUTION",
        "breach_levels": list(BREACH_LEVELS),
        "analysis_sessions": ANALYSIS_SESSIONS,
        "take_profit_applied": False,
        "stop_executed": False,
        "holdout_opened": False,
        "weights_unchanged": True,
        "selection_threshold_unchanged": True,
        "snapshot_failures": snapshot_failures,
        "observations": int(len(observations)),
        "download": {"requested": dl.requested, "successful": len(dl.successful), "failed": len(dl.failed)},
        "candidate_rules_promoted": False,
        "purpose": "Describe technical state at -5%/-7% breach and test whether development-derived one-factor directions persist OOS before any PROTECT/EXIT promotion.",
    }
    (outdir / "ETF_BREACH_STATE_SUMMARY.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
