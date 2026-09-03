from __future__ import annotations

import copy
import json
from pathlib import Path

from v182.backtest import etf_grok2_world_fallback_research_v3 as v3
from v182.backtest.etf_grok_research_backtest import _load_histories, _quality_eligible
from v182.backtest.etf_grok2_exit_rotation_research import _history_metrics, _world_benchmark
from v182.io.frames import load_master

ROOT = Path(__file__).resolve().parents[3]
WORLD_ISIN = "LU1681043599"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_perf63(histories, isin, d):
    a = _history_metrics(histories[isin], d).get("perf63")
    w = _history_metrics(histories[WORLD_ISIN], d).get("perf63")
    if a is None or w is None:
        return None
    return float(a - w)


def run(root: Path = ROOT, start: str = "2013-01-01", end: str | None = None) -> dict:
    base_cfg = _json(root / "config/V20.8_ETF_GROK_HIGH_PRECISION.json")
    g2_cfg = _json(root / "config/ETF_GROK2_CDC_V1.json")
    v3_cfg = _json(root / "config/ETF_GROK2_EXIT_RESEARCH_V3.json")
    cfg = _json(root / "config/ETF_GROK2_ALPHA_ENTRY_RESEARCH_V4.json")
    histories = _load_histories(root)
    allowed = set(_quality_eligible(root))
    ref = load_master(root / "inputs/V18.2_PEA_ETF_MASTER.csv")
    end = end or str(max(f.index.max() for f in histories.values() if not f.empty).date())

    g2_cfg = copy.deepcopy(g2_cfg)
    g2_cfg["quantitative_core"]["selection_threshold"] = float(cfg["base_selection_threshold"])

    initial = float(v3_cfg["portfolio"]["initial_capital_eur"])
    fee = float(v3_cfg["portfolio"]["active_entry_fee_bps"]) / 10000.0
    world = _world_benchmark(histories, __import__("pandas").Timestamp(start), __import__("pandas").Timestamp(end), initial, fee)

    original_universe = v3._research_universe_as_of
    outdir = root / "outputs/etf_grok2_alpha_entry_research_v4"
    outdir.mkdir(parents=True, exist_ok=True)
    variants = {}

    try:
        for rel_floor in cfg["entry_relative_perf63_minimums"]:
            rel_floor = float(rel_floor)

            def filtered_universe(histories_arg, allowed_arg, d, floor=rel_floor):
                dynamic_allowed = set()
                for isin in allowed_arg:
                    if isin == WORLD_ISIN or isin not in histories_arg:
                        continue
                    rel = _relative_perf63(histories_arg, isin, d)
                    if rel is not None and rel >= floor:
                        dynamic_allowed.add(isin)
                return original_universe(histories_arg, dynamic_allowed, d)

            v3._research_universe_as_of = filtered_universe
            key = f"REL63_{int(round(rel_floor * 100)):02d}PT"
            stats, trades, equity = v3.simulate_world_variant(
                "E_WORLD_FALLBACK", histories, allowed, ref, base_cfg, g2_cfg, v3_cfg, start, end
            )
            stats["entry_relative_perf63_floor"] = rel_floor
            stats["cagr_delta_vs_world"] = float(stats["cagr"] - world["cagr"])
            stats["final_equity_delta_vs_world"] = float(stats["final_equity"] - world["final_equity"])
            variants[key] = stats
            trades.to_csv(outdir / f"{key}_TRADES.csv", index=False)
            equity.to_csv(outdir / f"{key}_EQUITY.csv", index=False)
    finally:
        v3._research_universe_as_of = original_universe

    ranked = sorted(
        variants,
        key=lambda k: (variants[k]["cagr_delta_vs_world"], variants[k].get("calmar") or -999),
        reverse=True,
    )
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
        "best_variant": best,
        "best_cagr_delta_vs_world": variants[best]["cagr_delta_vs_world"],
        "beats_world": bool(variants[best]["cagr_delta_vs_world"] > 0),
        "meets_plus_2pt_hurdle": bool(variants[best]["cagr_delta_vs_world"] >= hurdle),
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
