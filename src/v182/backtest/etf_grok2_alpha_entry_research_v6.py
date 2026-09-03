from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd

from v182.backtest import etf_grok2_world_fallback_research_v3 as v3
from v182.backtest.etf_grok_research_backtest import _load_histories, _quality_eligible
from v182.backtest.etf_grok2_exit_rotation_research import _world_benchmark
from v182.io.frames import load_master

ROOT = Path(__file__).resolve().parents[3]
WORLD_ISIN = "LU1681043599"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _perf(frame: pd.DataFrame, d: pd.Timestamp, sessions: int) -> float | None:
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna().sort_index()
    close = close.loc[close.index <= d]
    if len(close) < sessions + 1:
        return None
    return float(close.iloc[-1] / close.iloc[-(sessions + 1)] - 1.0)


def _relative_perf(histories, isin: str, d: pd.Timestamp, sessions: int) -> float | None:
    a = _perf(histories[isin], d, sessions)
    w = _perf(histories[WORLD_ISIN], d, sessions)
    if a is None or w is None:
        return None
    return float(a - w)


def run(root: Path = ROOT) -> dict:
    base_cfg = _json(root / "config/V20.8_ETF_GROK_HIGH_PRECISION.json")
    g2_cfg = _json(root / "config/ETF_GROK2_CDC_V1.json")
    v3_cfg = _json(root / "config/ETF_GROK2_EXIT_RESEARCH_V3.json")
    cfg = _json(root / "config/ETF_GROK2_ALPHA_ENTRY_RESEARCH_V6.json")
    histories = _load_histories(root)
    allowed = set(_quality_eligible(root))
    ref = load_master(root / "inputs/V18.2_PEA_ETF_MASTER.csv")

    g2_cfg = copy.deepcopy(g2_cfg)
    g2_cfg["quantitative_core"]["selection_threshold"] = float(cfg["base_selection_threshold"])
    initial = float(v3_cfg["portfolio"]["initial_capital_eur"])
    fee = float(v3_cfg["portfolio"]["active_entry_fee_bps"]) / 10000.0
    original_universe = v3._research_universe_as_of
    outdir = root / "outputs/etf_grok2_alpha_entry_research_v6"
    outdir.mkdir(parents=True, exist_ok=True)

    result_variants = {}
    try:
        for spec in cfg["variants"]:
            per_period = {}
            total_closed = 0
            deltas = []
            for period in cfg["periods"]:
                f63, f126, f252 = float(spec["rel63_min"]), float(spec["rel126_min"]), float(spec["rel252_min"])

                def filtered_universe(histories_arg, allowed_arg, d, a=f63, b=f126, c=f252):
                    dynamic_allowed = set()
                    for isin in allowed_arg:
                        if isin == WORLD_ISIN or isin not in histories_arg:
                            continue
                        r63 = _relative_perf(histories_arg, isin, d, 63)
                        r126 = _relative_perf(histories_arg, isin, d, 126)
                        r252 = _relative_perf(histories_arg, isin, d, 252)
                        if r63 is None or r126 is None or r252 is None:
                            continue
                        if r63 >= a and r126 >= b and r252 >= c:
                            dynamic_allowed.add(isin)
                    return original_universe(histories_arg, dynamic_allowed, d)

                v3._research_universe_as_of = filtered_universe
                start, end = period["start"], period["end"]
                world = _world_benchmark(histories, pd.Timestamp(start), pd.Timestamp(end), initial, fee)
                stats, trades, equity = v3.simulate_world_variant(
                    "E_WORLD_FALLBACK", histories, allowed, ref, base_cfg, g2_cfg, v3_cfg, start, end
                )
                delta = float(stats["cagr"] - world["cagr"])
                stats["world_cagr"] = float(world["cagr"])
                stats["cagr_delta_vs_world"] = delta
                stats["entry_rel63_min"] = f63
                stats["entry_rel126_min"] = f126
                stats["entry_rel252_min"] = f252
                per_period[period["id"]] = stats
                total_closed += int(stats["closed_trades"])
                deltas.append(delta)
                trades.to_csv(outdir / f"{spec['id']}_{period['id']}_TRADES.csv", index=False)
                equity.to_csv(outdir / f"{spec['id']}_{period['id']}_EQUITY.csv", index=False)

            robust = cfg["robustness"]
            passes_periods = all(x > 0.0 for x in deltas)
            passes_trades = total_closed >= int(robust["minimum_total_closed_trades_across_periods"])
            result_variants[spec["id"]] = {
                "periods": per_period,
                "minimum_cagr_delta_vs_world": float(min(deltas)),
                "mean_cagr_delta_vs_world": float(sum(deltas) / len(deltas)),
                "total_closed_trades_across_periods": total_closed,
                "cross_period_robustness_pass": bool(passes_periods and passes_trades),
            }
    finally:
        v3._research_universe_as_of = original_universe

    ranking = sorted(
        result_variants,
        key=lambda k: (
            result_variants[k]["minimum_cagr_delta_vs_world"],
            result_variants[k]["mean_cagr_delta_vs_world"],
            result_variants[k]["total_closed_trades_across_periods"],
        ),
        reverse=True,
    )
    robust = [k for k in ranking if result_variants[k]["cross_period_robustness_pass"]]
    result = {
        "version": cfg["version"],
        "status": cfg["status"],
        "variants": result_variants,
        "ranking_by_cross_period_floor": ranking,
        "robust_world_beaters": robust,
        "has_cross_period_world_beater": bool(robust),
        "best_variant": ranking[0],
        "fixed_take_profit_used": False,
        "same_close_signal_execution_used": False,
        "rotation_used": False,
        "survivorship_bias_resolved": False,
        "promotion_eligible": False,
        "real_orders_allowed": False,
    }
    (outdir / "SUMMARY.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
