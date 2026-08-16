from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.backtest.exceptional_pit_oos import HOLDOUT_START, ROOT, etf_core_backtest
from v182.backtest.pit_oos_governed import enforce_single_position_per_isin
from v182.backtest.risk_beta_pit_oos import (
    ETF_CACHE_REL,
    _distribution_metrics,
    _relative_improvement,
    _ticker_to_isin,
    attach_pit_risk_features,
)
from v182.backtest.risk_beta_regime_pit_oos import _cum_return, _num
from v182.features.etf_mt_v2081 import load_histories_from_cache
from v182.risk.beta_metrics import build_common_benchmark, load_cached_prices

PROTOCOL_PATH = Path("config/BETA_RISK_REGIME_PIT_OOS_PROTOCOL_V1_2.json")
OUTDIR_REL = Path("outputs/backtest/beta_risk_regime_v12_pit_oos_2026_08_16")


def regime_v12_multiplier(
    *,
    downside_beta_252d: float | None,
    beta_63d: float | None,
    beta_252d: float | None,
    benchmark_return_21d: float | None,
    benchmark_return_63d: float | None,
    protocol: dict,
) -> tuple[float, bool, list[str]]:
    spec = protocol["primary_intervention"]
    mandatory = spec["mandatory"]
    confirmation = spec["confirmation_any"]
    inputs = (
        downside_beta_252d,
        beta_63d,
        beta_252d,
        benchmark_return_21d,
        benchmark_return_63d,
    )
    if any(value is None for value in inputs):
        return float(spec["otherwise_position_multiplier"]), False, ["MISSING_TRIGGER_INPUT"]

    high_downside = float(downside_beta_252d) >= float(mandatory["downside_beta_252d_min"])
    acceleration = float(beta_63d) - float(beta_252d)
    checks = {
        "BETA_ACCELERATING": acceleration >= float(confirmation["beta_acceleration_63d_minus_252d_min"]),
        "MARKET_21D_NEGATIVE": float(benchmark_return_21d) <= float(confirmation["benchmark_return_21d_max"]),
        "MARKET_63D_NEGATIVE": float(benchmark_return_63d) <= float(confirmation["benchmark_return_63d_max"]),
    }
    confirmation_count = sum(checks.values())
    fired = high_downside and confirmation_count >= int(spec["minimum_confirmation_count"])
    reasons = (["HIGH_DOWNSIDE_BETA"] if high_downside else []) + [
        name for name, passed in checks.items() if passed
    ]
    multiplier = spec["triggered_position_multiplier"] if fired else spec["otherwise_position_multiplier"]
    return float(multiplier), fired, reasons


def attach_v12_intervention(
    trades: pd.DataFrame,
    benchmark_returns: pd.Series,
    protocol: dict,
) -> pd.DataFrame:
    out = trades.copy()
    multipliers: list[float] = []
    triggers: list[bool] = []
    reasons: list[str] = []
    market_21: list[float | None] = []
    market_63: list[float | None] = []
    adjusted: list[float | None] = []

    for _, row in out.iterrows():
        signal = pd.Timestamp(row.get("signal_date"))
        if signal.tzinfo is not None:
            signal = signal.tz_localize(None)
        history = benchmark_returns.loc[benchmark_returns.index <= signal]
        ret21 = _cum_return(history, 21)
        ret63 = _cum_return(history, 63)
        multiplier, fired, reason_codes = regime_v12_multiplier(
            downside_beta_252d=_num(row.get("risk_downside_beta_252d")),
            beta_63d=_num(row.get("risk_beta_63d")),
            beta_252d=_num(row.get("risk_beta_252d")),
            benchmark_return_21d=ret21,
            benchmark_return_63d=ret63,
            protocol=protocol,
        )
        net_return = _num(row.get("net_return"))
        multipliers.append(multiplier)
        triggers.append(fired)
        reasons.append("|".join(reason_codes))
        market_21.append(ret21)
        market_63.append(ret63)
        adjusted.append(net_return * multiplier if net_return is not None else None)

    out["regime_v12_benchmark_return_21d"] = market_21
    out["regime_v12_benchmark_return_63d"] = market_63
    out["regime_v12_beta_acceleration_63d_minus_252d"] = (
        pd.to_numeric(out.get("risk_beta_63d"), errors="coerce")
        - pd.to_numeric(out.get("risk_beta_252d"), errors="coerce")
    )
    out["regime_v12_risk_trigger"] = triggers
    out["regime_v12_reason_codes"] = reasons
    out["regime_v12_position_multiplier"] = multipliers
    out["regime_v12_adjusted_net_return"] = adjusted
    return out


def _period_evaluation(frame: pd.DataFrame, period: str, gates: dict) -> dict:
    subset = frame[frame["period"].astype(str).eq(period)].copy()
    baseline = _distribution_metrics(subset, "net_return")
    adjusted = _distribution_metrics(subset, "regime_v12_adjusted_net_return")
    beta_present = pd.to_numeric(subset.get("risk_beta_252d"), errors="coerce").notna()
    coverage = float(beta_present.mean()) if len(subset) else 0.0
    triggered = int(
        subset.get("regime_v12_risk_trigger", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()
    )
    ra_improvement = _relative_improvement(
        adjusted.get("risk_adjusted_mean_std"), baseline.get("risk_adjusted_mean_std")
    )
    base_exp = baseline.get("expectancy")
    adj_exp = adjusted.get("expectancy")
    if base_exp is not None and adj_exp is not None and base_exp > 0:
        expectancy_retention = adj_exp / base_exp
    elif base_exp is not None and adj_exp is not None:
        expectancy_retention = 1.0 if adj_exp >= base_exp else 0.0
    else:
        expectancy_retention = None

    checks = {
        "minimum_trades": baseline["n"] >= int(gates["minimum_trades_each_oos_period"]),
        "minimum_beta_coverage": coverage >= float(gates["minimum_beta_coverage"]),
        "minimum_triggered_trades": triggered >= int(gates["minimum_triggered_trades_each_oos_period"]),
        "risk_adjusted_improvement": ra_improvement is not None
        and ra_improvement >= float(gates["minimum_risk_adjusted_improvement"]),
        "expectancy_retention": expectancy_retention is not None
        and expectancy_retention >= float(gates["minimum_expectancy_retention"]),
        "positive_adjusted_expectancy": adjusted.get("expectancy") is not None
        and adjusted["expectancy"] > 0,
        "p05_tail_not_worse": adjusted.get("p05") is not None
        and baseline.get("p05") is not None
        and adjusted["p05"] >= baseline["p05"],
        "sequence_max_drawdown_not_worse": adjusted.get("sequence_max_drawdown") is not None
        and baseline.get("sequence_max_drawdown") is not None
        and adjusted["sequence_max_drawdown"] >= baseline["sequence_max_drawdown"],
    }
    return {
        "period": period,
        "baseline": baseline,
        "regime_v12_adjusted": adjusted,
        "beta_coverage": round(coverage, 6),
        "triggered_trades": triggered,
        "risk_adjusted_improvement": round(ra_improvement, 6) if ra_improvement is not None else None,
        "expectancy_retention": round(expectancy_retention, 6) if expectancy_retention is not None else None,
        "checks": checks,
        "pass": all(checks.values()),
    }


def run(root: Path = ROOT) -> dict:
    protocol = json.loads((root / PROTOCOL_PATH).read_text(encoding="utf-8"))
    if protocol.get("locked_before_results") is not True:
        raise RuntimeError("PROTOCOL_NOT_PRE_REGISTERED")
    if protocol.get("final_holdout_opened") is not False:
        raise RuntimeError("FINAL_HOLDOUT_MUST_REMAIN_CLOSED")

    raw_trades, baseline_summary = etf_core_backtest(root)
    trades, blocked = enforce_single_position_per_isin(raw_trades)
    if trades.empty:
        return {"status": "BLOCKED_NO_ETF_TRADES", "baseline": baseline_summary}
    exit_ts = pd.to_datetime(trades["exit_date"], errors="coerce")
    if (exit_ts >= HOLDOUT_START).any():
        raise RuntimeError("FINAL_HOLDOUT_LABEL_EXPOSURE_DETECTED")

    action_prices = load_cached_prices(root / "data" / "cache" / "actions")
    benchmark, benchmark_diag = build_common_benchmark(
        action_prices, min_sessions=126, min_constituents=20
    )
    if benchmark is None:
        return {"status": "BLOCKED_ACTION_BENCHMARK_UNAVAILABLE", "benchmark": benchmark_diag}
    benchmark = benchmark.sort_index()
    if benchmark.index.tz is not None:
        benchmark.index = benchmark.index.tz_localize(None)

    histories = load_histories_from_cache(root / ETF_CACHE_REL, _ticker_to_isin(root))
    enriched = attach_pit_risk_features(trades, histories, benchmark)
    enriched = attach_v12_intervention(enriched, benchmark, protocol)

    signal_guard = pd.to_datetime(enriched["signal_date"], errors="coerce")
    asset_guard = pd.to_datetime(enriched.get("risk_lookahead_guard_asset_max"), errors="coerce")
    benchmark_guard = pd.to_datetime(
        enriched.get("risk_lookahead_guard_benchmark_max"), errors="coerce"
    )
    if ((asset_guard > signal_guard) | (benchmark_guard > signal_guard)).fillna(False).any():
        raise RuntimeError("RISK_FEATURE_LOOKAHEAD_DETECTED")

    gates = protocol["promotion_gates"]
    evaluations = [
        _period_evaluation(enriched, period, gates) for period in protocol["evaluation_periods"]
    ]
    total_triggered = int(enriched["regime_v12_risk_trigger"].fillna(False).astype(bool).sum())
    trigger_total_gate = total_triggered >= int(gates["minimum_triggered_trades_total"])
    promoted = trigger_total_gate and all(item["pass"] for item in evaluations)
    verdict = (
        "PROMOTE_REGIME_V12_RISK_REDUCTION"
        if promoted
        else "KEEP_REGIME_V12_RISK_REDUCTION_SHADOW"
    )

    outdir = root / OUTDIR_REL
    outdir.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(
        outdir / "ETF_MT_REGIME_V12_BETA_RISK_PIT_TRADES.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )
    payload = {
        "version": protocol["version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "SUCCESS",
        "scope": protocol["scope"],
        "protocol_locked_before_results": True,
        "design_basis": protocol["design_basis"],
        "final_holdout_start": HOLDOUT_START.date().isoformat(),
        "final_holdout_opened": False,
        "raw_trade_count": int(len(raw_trades)),
        "trade_count_after_single_position_guard": int(len(trades)),
        "overlapping_signals_blocked": int(len(blocked)),
        "benchmark": benchmark_diag,
        "triggered_trades_total": total_triggered,
        "trigger_total_gate_pass": trigger_total_gate,
        "evaluation": evaluations,
        "verdict": verdict,
        "promotion": {
            "regime_v12_risk_reduction": promoted,
            "score_or_decision_influence": False,
            "stop_loss_influence": False,
            "real_orders": False,
        },
        "governance": protocol["governance"],
    }
    (outdir / "BETA_RISK_REGIME_V12_PIT_OOS_SUMMARY.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
