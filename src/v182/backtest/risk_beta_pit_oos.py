from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math

import numpy as np
import pandas as pd

from v182.backtest.exceptional_pit_oos import HOLDOUT_START, ROOT, _read_csv, etf_core_backtest
from v182.backtest.pit_oos_governed import enforce_single_position_per_isin
from v182.features.etf_mt_v2081 import load_histories_from_cache
from v182.risk.beta_metrics import build_common_benchmark, compute_beta_metrics, load_cached_prices, to_returns

PROTOCOL_PATH = Path("config/BETA_RISK_PIT_OOS_PROTOCOL.json")
ETF_CACHE_REL = Path("data/cache/exceptional_pit_oos_etf")


def _ticker_to_isin(root: Path) -> dict[str, str]:
    path = root / "outputs" / "V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    if not path.exists():
        path = root / "inputs" / "V18.2_PEA_ETF_MASTER.csv"
    frame = _read_csv(path)
    if frame.empty or "isin" not in frame.columns:
        return {}
    ticker_col = "yahoo_ticker" if "yahoo_ticker" in frame.columns else "ticker_yahoo_final" if "ticker_yahoo_final" in frame.columns else None
    if ticker_col is None:
        return {}
    out: dict[str, str] = {}
    for ticker, isin in zip(frame[ticker_col], frame["isin"]):
        ticker_text = str(ticker).strip()
        isin_text = str(isin).strip()
        if ticker_text and ticker_text.lower() not in {"nan", "none", "<na>"} and isin_text:
            out[ticker_text] = isin_text
    return out


def _close_returns(history: pd.DataFrame) -> pd.Series | None:
    if history is None or history.empty:
        return None
    close_col = next((field for field in ("Close", "Adj Close", "close", "adj close") if field in history.columns), None)
    if close_col is None:
        return None
    close = pd.to_numeric(history[close_col], errors="coerce").dropna()
    if close.empty:
        return None
    idx = pd.DatetimeIndex(pd.to_datetime(close.index, errors="coerce"))
    mask = ~idx.isna()
    close = pd.Series(close.to_numpy()[mask], index=idx[mask]).sort_index()
    if close.index.tz is not None:
        close.index = close.index.tz_localize(None)
    close = close[~close.index.duplicated(keep="last")]
    return to_returns(close)


def beta_only_multiplier(beta_252d: float | None, downside_beta_252d: float | None) -> float:
    beta = downside_beta_252d if downside_beta_252d is not None and math.isfinite(downside_beta_252d) else beta_252d
    if beta is None or not math.isfinite(beta):
        return 1.0
    return round(max(0.50, min(1.00, 1.0 / max(1.0, float(beta)))), 6)


def _risk_metrics_at_signal(
    etf_returns: pd.Series,
    benchmark_returns: pd.Series,
    signal_date: pd.Timestamp,
) -> dict:
    cutoff = pd.Timestamp(signal_date).tz_localize(None) if pd.Timestamp(signal_date).tzinfo is not None else pd.Timestamp(signal_date)
    asset = etf_returns.loc[etf_returns.index <= cutoff]
    benchmark = benchmark_returns.loc[benchmark_returns.index <= cutoff]
    metrics = compute_beta_metrics(asset, benchmark)
    metrics["risk_feature_asof"] = cutoff.date().isoformat()
    metrics["lookahead_guard_asset_max"] = asset.index.max().date().isoformat() if not asset.empty else None
    metrics["lookahead_guard_benchmark_max"] = benchmark.index.max().date().isoformat() if not benchmark.empty else None
    return metrics


def attach_pit_risk_features(
    trades: pd.DataFrame,
    etf_histories: dict[str, pd.DataFrame],
    benchmark_returns: pd.Series,
) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    rows: list[dict] = []
    etf_returns = {isin: _close_returns(history) for isin, history in etf_histories.items()}
    for _, trade in trades.iterrows():
        record = trade.to_dict()
        isin = str(trade.get("isin") or "")
        signal_date = pd.Timestamp(trade.get("signal_date"))
        returns = etf_returns.get(isin)
        if returns is None or returns.empty:
            metrics = {"status": "MISSING_ETF_HISTORY"}
        else:
            metrics = _risk_metrics_at_signal(returns, benchmark_returns, signal_date)
        beta = metrics.get("beta_252d")
        downside = metrics.get("downside_beta_252d")
        beta_value = float(beta) if beta is not None and math.isfinite(float(beta)) else None
        downside_value = float(downside) if downside is not None and math.isfinite(float(downside)) else None
        multiplier = beta_only_multiplier(beta_value, downside_value)
        net_return = pd.to_numeric(pd.Series([trade.get("net_return")]), errors="coerce").iloc[0]
        record.update({f"risk_{key}": value for key, value in metrics.items()})
        record["risk_beta_only_multiplier"] = multiplier
        record["risk_adjusted_net_return"] = float(net_return) * multiplier if pd.notna(net_return) else np.nan
        rows.append(record)
    return pd.DataFrame(rows)


def _max_drawdown(values: pd.Series) -> float | None:
    returns = pd.to_numeric(values, errors="coerce").dropna()
    if returns.empty:
        return None
    equity = (1.0 + returns).cumprod()
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    return float(drawdown.min())


def _distribution_metrics(frame: pd.DataFrame, return_col: str) -> dict:
    values = pd.to_numeric(frame.get(return_col), errors="coerce").dropna()
    if values.empty:
        return {
            "n": 0,
            "wins": 0,
            "win_rate": None,
            "expectancy": None,
            "std": None,
            "risk_adjusted_mean_std": None,
            "p05": None,
            "worst": None,
            "sequence_max_drawdown": None,
            "cum_compound_return": None,
        }
    std = float(values.std(ddof=1)) if len(values) > 1 else None
    mean = float(values.mean())
    risk_adjusted = mean / std if std is not None and std > 0 else None
    return {
        "n": int(len(values)),
        "wins": int((values > 0).sum()),
        "win_rate": round(float((values > 0).mean()), 6),
        "expectancy": round(mean, 8),
        "std": round(std, 8) if std is not None else None,
        "risk_adjusted_mean_std": round(risk_adjusted, 8) if risk_adjusted is not None else None,
        "p05": round(float(values.quantile(0.05)), 8),
        "worst": round(float(values.min()), 8),
        "sequence_max_drawdown": round(float(_max_drawdown(values)), 8),
        "cum_compound_return": round(float((1.0 + values).prod() - 1.0), 8),
    }


def _relative_improvement(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or not math.isfinite(new) or not math.isfinite(old) or abs(old) < 1e-12:
        return None
    return new / old - 1.0


def _period_evaluation(frame: pd.DataFrame, period: str, gates: dict) -> dict:
    subset = frame[frame["period"].astype(str).eq(period)].copy()
    baseline = _distribution_metrics(subset, "net_return")
    adjusted = _distribution_metrics(subset, "risk_adjusted_net_return")
    beta_present = pd.to_numeric(subset.get("risk_beta_252d"), errors="coerce").notna()
    coverage = float(beta_present.mean()) if len(subset) else 0.0
    base_ra = baseline.get("risk_adjusted_mean_std")
    adj_ra = adjusted.get("risk_adjusted_mean_std")
    ra_improvement = _relative_improvement(adj_ra, base_ra)
    base_exp = baseline.get("expectancy")
    adj_exp = adjusted.get("expectancy")
    if base_exp is not None and adj_exp is not None and base_exp > 0:
        expectancy_retention = adj_exp / base_exp
    elif base_exp is not None and adj_exp is not None:
        expectancy_retention = 1.0 if adj_exp >= base_exp else 0.0
    else:
        expectancy_retention = None
    p05_improved = (
        baseline.get("p05") is not None
        and adjusted.get("p05") is not None
        and adjusted["p05"] >= baseline["p05"]
    )
    drawdown_improved = (
        baseline.get("sequence_max_drawdown") is not None
        and adjusted.get("sequence_max_drawdown") is not None
        and adjusted["sequence_max_drawdown"] >= baseline["sequence_max_drawdown"]
    )
    checks = {
        "minimum_trades": baseline["n"] >= int(gates["minimum_trades_each_oos_period"]),
        "minimum_beta_coverage": coverage >= float(gates["minimum_beta_coverage"]),
        "risk_adjusted_improvement": ra_improvement is not None and ra_improvement >= float(gates["minimum_risk_adjusted_improvement"]),
        "expectancy_retention": expectancy_retention is not None and expectancy_retention >= float(gates["minimum_expectancy_retention"]),
        "p05_tail_improvement": p05_improved if gates.get("require_p05_tail_improvement", True) else True,
        "sequence_max_drawdown_improvement": drawdown_improved if gates.get("require_sequence_max_drawdown_improvement", True) else True,
    }
    return {
        "period": period,
        "baseline": baseline,
        "beta_sized": adjusted,
        "beta_coverage": round(coverage, 6),
        "risk_adjusted_improvement": round(ra_improvement, 6) if ra_improvement is not None else None,
        "expectancy_retention": round(expectancy_retention, 6) if expectancy_retention is not None else None,
        "checks": checks,
        "pass": all(checks.values()),
    }


def run(root: Path = ROOT) -> dict:
    protocol = json.loads((root / PROTOCOL_PATH).read_text(encoding="utf-8"))
    if protocol.get("final_holdout_opened") is not False:
        raise RuntimeError("FINAL_HOLDOUT_MUST_REMAIN_CLOSED")
    if protocol.get("locked_before_results") is not True:
        raise RuntimeError("PROTOCOL_NOT_PRE_REGISTERED")

    raw_trades, baseline_summary = etf_core_backtest(root)
    trades, blocked = enforce_single_position_per_isin(raw_trades)
    if trades.empty:
        return {"status": "BLOCKED_NO_ETF_TRADES", "baseline": baseline_summary}
    trades["signal_ts"] = pd.to_datetime(trades["signal_date"], errors="coerce")
    trades["exit_ts"] = pd.to_datetime(trades["exit_date"], errors="coerce")
    if (trades["exit_ts"] >= HOLDOUT_START).any():
        raise RuntimeError("FINAL_HOLDOUT_LABEL_EXPOSURE_DETECTED")

    action_prices = load_cached_prices(root / "data" / "cache" / "actions")
    benchmark, benchmark_diag = build_common_benchmark(action_prices, min_sessions=126, min_constituents=20)
    if benchmark is None:
        return {
            "status": "BLOCKED_ACTION_BENCHMARK_UNAVAILABLE",
            "benchmark": benchmark_diag,
            "baseline": baseline_summary,
        }
    benchmark_returns = benchmark.sort_index()
    if benchmark_returns.index.tz is not None:
        benchmark_returns.index = benchmark_returns.index.tz_localize(None)

    ticker_to_isin = _ticker_to_isin(root)
    etf_histories = load_histories_from_cache(root / ETF_CACHE_REL, ticker_to_isin)
    enriched = attach_pit_risk_features(trades, etf_histories, benchmark_returns)
    if not enriched.empty:
        asset_guard = pd.to_datetime(enriched.get("risk_lookahead_guard_asset_max"), errors="coerce")
        bench_guard = pd.to_datetime(enriched.get("risk_lookahead_guard_benchmark_max"), errors="coerce")
        signal_guard = pd.to_datetime(enriched["signal_date"], errors="coerce")
        if ((asset_guard > signal_guard) | (bench_guard > signal_guard)).fillna(False).any():
            raise RuntimeError("RISK_FEATURE_LOOKAHEAD_DETECTED")

    gates = protocol["promotion_gates"]
    evaluations = [_period_evaluation(enriched, period, gates) for period in protocol["evaluation_periods"]]
    all_pass = all(item["pass"] for item in evaluations) if evaluations else False
    verdict = "PROMOTE_BETA_ONLY_SIZING" if all_pass else "KEEP_BETA_SIZING_SHADOW"

    outdir = root / "outputs" / "backtest" / "beta_risk_pit_oos_2026_08_16"
    outdir.mkdir(parents=True, exist_ok=True)
    enriched.drop(columns=["signal_ts", "exit_ts"], errors="ignore").to_csv(
        outdir / "ETF_MT_BETA_RISK_PIT_TRADES.csv", sep=";", index=False, encoding="utf-8-sig"
    )
    payload = {
        "version": protocol["version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SUCCESS",
        "scope": protocol["scope"],
        "protocol_locked_before_results": True,
        "final_holdout_start": HOLDOUT_START.date().isoformat(),
        "final_holdout_opened": False,
        "raw_trade_count": int(len(raw_trades)),
        "trade_count_after_single_position_guard": int(len(trades)),
        "overlapping_signals_blocked": int(len(blocked)),
        "benchmark": benchmark_diag,
        "evaluation": evaluations,
        "verdict": verdict,
        "promotion": {
            "beta_only_position_sizing": all_pass,
            "score_or_decision_influence": False,
            "stop_loss_influence": False,
            "exact_holdings_overlap": False,
            "economic_engine_overlap_sizing": False,
            "sector_correction_overlay": False,
            "action_full_model_sizing": False,
        },
        "limitations": [
            "Current-universe survivorship bias remains in the equal-weight PEA Action benchmark and ETF universe.",
            "This is a marginal diagnostic on the historical ETF MT 38-dynamic PIT core, not certification of the 43-criterion composite or full 268-criterion referential.",
            "Sequence max drawdown compounds trade outcomes in signal order; it is not a timestamped multi-position portfolio NAV drawdown.",
            "Economic-engine and exact-holdings overlap are deliberately excluded because historical PIT holdings/classification snapshots are incomplete.",
        ],
        "governance": protocol["governance"],
    }
    (outdir / "BETA_RISK_PIT_OOS_SUMMARY.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
