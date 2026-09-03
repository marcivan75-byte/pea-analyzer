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


def _positive_year_fraction(stats: dict, world: dict) -> float:
    common = sorted(set(stats.get("annual_returns", {})) & set(world.get("annual_returns", {})))
    if not common:
        return 0.0
    wins = sum(stats["annual_returns"][y] > world["annual_returns"][y] for y in common)
    return float(wins / len(common))


def run(root: Path = ROOT, start: str = "2013-01-01", end: str | None = None) -> dict:
    base_cfg = _json(root / "config/V20.8_ETF_GROK_HIGH_PRECISION.json")
    g2_cfg = _json(root / "config/ETF_GROK2_CDC_V1.json")
    v3_cfg = _json(root / "config/ETF_GROK2_EXIT_RESEARCH_V3.json")
    cfg = _json(root / "config/ETF_GROK2_ALPHA_ENTRY_RESEARCH_V5.json")
    histories = _load_histories(root)
    allowed = set(_quality_eligible(root))
    ref = load_master(root / "inputs/V18.2_PEA_ETF_MASTER.csv")
    end = end or str(max(f.index.max() for f in histories.values() if not f.empty).date())

    g2_cfg = copy.deepcopy(g2_cfg)
    g2_cfg["quantitative_core"]["selection_threshold"] = float(cfg["base_selection_threshold"])

    initial = float(v3_cfg["portfolio"]["initial_capital_eur"])
    fee = float(v3_cfg["portfolio"]["active_entry_fee_bps"]) / 10000.0
    world = _world_benchmark(histories, pd.Timestamp(start), pd.Timestamp(end), initial, fee)

    original_universe = v3._research_universe_as_of
    outdir = root / "outputs/etf_grok2_alpha_entry_research_v5"
    outdir.mkdir(parents=True, exist_ok=True)
    variants = {}

    try:
        for spec in cfg["variants"]:
            rel63_min = float(spec["rel63_min"])
            rel126_min = spec.get("rel126_min")
            rel126_min = None if rel126_min is None else float(rel126_min)

            def filtered_universe(histories_arg, allowed_arg, d, f63=rel63_min, f126=rel126_min):
                dynamic_allowed = set()
                for isin in allowed_arg:
                    if isin == WORLD_ISIN or isin not in histories_arg:
                        continue
                    r63 = _relative_perf(histories_arg, isin, d, 63)
                    if r63 is None or r63 < f63:
                        continue
                    if f126 is not None:
                        r126 = _relative_perf(histories_arg, isin, d, 126)
                        if r126 is None or r126 < f126:
                            continue
                    dynamic_allowed.add(isin)
                return original_universe(histories_arg, dynamic_allowed, d)

            v3._research_universe_as_of = filtered_universe
            key = str(spec["id"])
            stats, trades, equity = v3.simulate_world_variant(
                "E_WORLD_FALLBACK", histories, allowed, ref, base_cfg, g2_cfg, v3_cfg, start, end
            )
            stats["entry_relative_perf63_floor"] = rel63_min
            stats["entry_relative_perf126_floor"] = rel126_min
            stats["cagr_delta_vs_world"] = float(stats["cagr"] - world["cagr"])
            stats["final_equity_delta_vs_world"] = float(stats["final_equity"] - world["final_equity"])
            stats["positive_year_fraction_vs_world"] = _positive_year_fraction(stats, world)
            robust = cfg["robustness"]
            stats["robustness_pass"] = bool(
                stats["closed_trades"] >= int(robust["minimum_closed_trades"])
                and stats["positive_year_fraction_vs_world"] >= float(robust["minimum_positive_year_fraction"])
                and stats["cagr_delta_vs_world"] > 0.0
            )
            variants[key] = stats
            trades.to_csv(outdir / f"{key}_TRADES.csv", index=False)
            equity.to_csv(outdir / f"{key}_EQUITY.csv", index=False)
    finally:
        v3._research_universe_as_of = original_universe

    ranked = sorted(variants, key=lambda k: (variants[k]["cagr_delta_vs_world"], variants[k]["closed_trades"]), reverse=True)
    robust_ranked = [k for k in ranked if variants[k]["robustness_pass"]]
    best = ranked[0]
    hurdle = float(cfg["objective"]["promotion_hurdle_cagr_points"])
    result = {
        "version": cfg["version"],
        "status": cfg["status"],
        "start": start,
        "end": end,
        "world_benchmark": world,
        "variants": variants,
        "ranking_by_cagr_vs_world": ranked,
        "robustness_pass_variants": robust_ranked,
        "best_variant": best,
        "best_cagr_delta_vs_world": variants[best]["cagr_delta_vs_world"],
        "beats_world": bool(variants[best]["cagr_delta_vs_world"] > 0),
        "meets_plus_2pt_hurdle": bool(variants[best]["cagr_delta_vs_world"] >= hurdle),
        "has_robust_world_beater": bool(robust_ranked),
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
