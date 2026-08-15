from __future__ import annotations

from pathlib import Path
import json
import math
import pandas as pd

from v182.backtest.exceptional_pit_oos import (
    HOLDOUT_START,
    ROUND_TRIP_COST_BPS,
    _naive_index,
    _profit_factor,
    _wilson_lower,
    etf_core_backtest,
    _simulate_etf_trade,
    _read_csv,
    _read_json,
)
from v182.features.etf_mt_v2081 import build_equal_weight_market_proxy, load_histories_from_cache, score_snapshot
from v182.sources.yfinance_bulk import download_history

ROOT = Path(__file__).resolve().parents[3]
STOP_GRID = (-0.04, -0.05, -0.06, -0.07, -0.08, -0.10, -0.12, -0.15, -0.18)


def _metrics(frame: pd.DataFrame, stop_return: float, period: str) -> dict:
    values = pd.to_numeric(frame.get("net_return"), errors="coerce").dropna()
    wins = int((values > 0).sum()) if not values.empty else 0
    pf = _profit_factor(values)
    return {
        "stop_return": stop_return,
        "stop_pct": round(abs(stop_return) * 100.0, 2),
        "period": period,
        "trades": int(len(values)),
        "wins": wins,
        "win_rate": round(wins / len(values), 6) if len(values) else None,
        "wilson_95_lower": round(float(_wilson_lower(wins, len(values))), 6) if len(values) else None,
        "expectancy_net": round(float(values.mean()), 8) if len(values) else None,
        "profit_factor_net": None if pf is None else ("INF" if math.isinf(pf) else round(float(pf), 6)),
        "cum_net_return_sum": round(float(values.sum()), 8) if len(values) else None,
        "worst_trade_net": round(float(values.min()), 8) if len(values) else None,
    }


def run(root: Path = ROOT) -> dict:
    etf_path = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not etf_path.exists():
        etf_path = root / "inputs" / "V18.2_PEA_ETF_MASTER.csv"
    etfs = _read_csv(etf_path)
    ticker_col = "yahoo_ticker" if "yahoo_ticker" in etfs.columns else "ticker_yahoo_final"
    ticker_to_isin = {
        str(t).strip(): str(i).strip()
        for t, i in zip(etfs[ticker_col], etfs["isin"])
        if str(t).strip() and str(t).strip().lower() not in {"nan", "none", "<na>"}
    }
    cache = root / "data" / "cache" / "stop_sensitivity_etf"
    result = download_history(list(ticker_to_isin), str(cache), period="max", interval="1d", batch_size=30, auto_adjust=True, include_actions=False)
    histories = load_histories_from_cache(cache, ticker_to_isin)
    cfg = _read_json(root / "config" / "V20.8_ETF_MT_HIGH_PRECISION.json")
    proxy = build_equal_weight_market_proxy(histories)
    proxy_index = _naive_index(proxy.index)
    month_dates = pd.Series(proxy_index, index=proxy_index).groupby(proxy_index.to_period("M")).last().tolist()
    start_date = proxy_index[min(756, len(proxy_index)-1)]
    dates = [pd.Timestamp(d) for d in month_dates if pd.Timestamp(d) >= start_date and pd.Timestamp(d) < HOLDOUT_START]
    target_return = float(cfg["exit_policy"]["target_return"])
    max_hold = int(cfg["exit_policy"]["max_holding_sessions"])

    rows = []
    active_until: dict[str, pd.Timestamp] = {}
    blocked = 0
    for signal_date in dates:
        sliced = {k: v.loc[:signal_date].copy() for k, v in histories.items() if not v.loc[:signal_date].empty}
        snapshot, _ = score_snapshot(sliced, etfs, cfg)
        selected = snapshot[snapshot["selected"] == True]  # noqa: E712
        for _, r in selected.iterrows():
            isin = str(r["instrument_id"])
            for stop_return in STOP_GRID:
                key = f"{isin}|{stop_return}"
                if key in active_until and signal_date < active_until[key]:
                    blocked += 1
                    continue
                trade = _simulate_etf_trade(histories[isin], signal_date, target_return, stop_return, max_hold)
                if trade is None:
                    continue
                active_until[key] = pd.Timestamp(trade["exit_date"])
                period = "DEVELOPMENT" if signal_date.year <= 2020 else "VALIDATION_OOS" if signal_date.year <= 2023 else "DIAGNOSTIC_OOS"
                rows.append({"signal_date": signal_date.date().isoformat(), "period": period, "isin": isin, "score_final": float(r["score_final"]), "stop_return": stop_return, **trade})

    trades = pd.DataFrame(rows)
    metrics = []
    for stop_return in STOP_GRID:
        subset = trades[trades["stop_return"] == stop_return] if not trades.empty else pd.DataFrame()
        for period in ("DEVELOPMENT", "VALIDATION_OOS", "DIAGNOSTIC_OOS"):
            metrics.append(_metrics(subset[subset["period"] == period] if not subset.empty else pd.DataFrame(), stop_return, period))
    m = pd.DataFrame(metrics)
    outdir = root / "outputs" / "backtest" / "stop_sensitivity_2026_08_15"
    outdir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(outdir / "ETF_STOP_SENSITIVITY_TRADES.csv", sep=";", index=False, encoding="utf-8-sig")
    m.to_csv(outdir / "ETF_STOP_SENSITIVITY_METRICS.csv", sep=";", index=False, encoding="utf-8-sig")
    payload = {
        "status": "SUCCESS",
        "model": "V20.8.1_EXACT_38_DYNAMIC_PIT_CORE",
        "target_return": target_return,
        "stop_grid": list(STOP_GRID),
        "holdout_opened": False,
        "single_position_per_isin": True,
        "overlapping_signals_blocked": blocked,
        "download": {"requested": result.requested, "successful": len(result.successful), "failed": len(result.failed)},
        "selection_rule_unchanged": True,
        "weights_unchanged": True,
        "thresholds_unchanged": True,
        "note": "Stop sensitivity only. No model-weight optimisation and no final holdout use.",
    }
    (outdir / "ETF_STOP_SENSITIVITY_SUMMARY.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
