from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from v182.backtest.etf_grok_research_backtest import (
    COST_PER_SIDE,
    _benchmark_return,
    _load_histories,
    _monthly_signal_dates,
    _net_return,
    _next_row,
    _quality_eligible,
    _research_universe_as_of,
    _stats,
)
from v182.features.etf_grok_v2081 import build_equal_weight_market_proxy
from v182.features.etf_grok2_cdc import score_grok2
from v182.io.frames import load_master

ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Grok2Trade:
    isin: str
    peer_group: str
    signal_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    gross_return: float
    net_return: float
    benchmark_return: float | None
    excess_return: float | None
    holding_sessions: int
    exit_reason: str
    score_final: float


def _load_json(root: Path, rel: str) -> dict:
    return json.loads((root / rel).read_text(encoding="utf-8"))


def _simulate_grok2_exit(frame: pd.DataFrame, entry_date: pd.Timestamp, entry_price: float, cfg: dict) -> tuple[pd.Timestamp, float, int, str] | None:
    policy = cfg["exit_policy"]
    min_hold = int(policy["minimum_holding_before_thesis_break_sessions"])
    hard_stop = float(policy["hard_risk_stop_return"])
    max_hold = int(policy["max_holding_sessions"])
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna().sort_index()
    if close.empty:
        return None
    sma200 = close.rolling(200).mean()
    perf6 = close / close.shift(126) - 1.0
    path = close.loc[close.index >= entry_date]
    if path.empty:
        return None
    for i, (d, px) in enumerate(path.items()):
        px = float(px)
        ret = px / entry_price - 1.0
        if ret <= hard_stop:
            return pd.Timestamp(d), px, i, "HARD_RISK_STOP"
        if i >= min_hold:
            s = sma200.loc[d] if d in sma200.index else pd.NA
            p6 = perf6.loc[d] if d in perf6.index else pd.NA
            if pd.notna(s) and pd.notna(p6) and px < float(s) and float(p6) <= 0.0:
                return pd.Timestamp(d), px, i, "THESIS_BREAK_TREND"
        if i >= max_hold:
            return pd.Timestamp(d), px, i, "HORIZON_REVIEW_CLOSE"
    return pd.Timestamp(path.index[-1]), float(path.iloc[-1]), max(0, len(path) - 1), "END_OF_DATA"


def _segment_stats(trades: pd.DataFrame, start: str, end: str) -> dict:
    if trades.empty:
        return _stats(trades)
    d = pd.to_datetime(trades["entry_date"], errors="coerce")
    seg = trades.loc[(d >= pd.Timestamp(start)) & (d <= pd.Timestamp(end))]
    return _stats(seg)


def run(root: Path = ROOT, start: str = "2013-01-01", end: str | None = None) -> dict:
    base_cfg = _load_json(root, "config/V20.8_ETF_GROK_HIGH_PRECISION.json")
    g2_cfg = _load_json(root, "config/ETF_GROK2_CDC_V1.json")
    frozen_v1 = _load_json(root, "config/ETF_GROK_V1_FROZEN_BENCHMARK.json")
    histories = _load_histories(root)
    allowed = _quality_eligible(root)
    ref = load_master(root / "inputs" / "V18.2_PEA_ETF_MASTER.csv")
    global_end = max(f.index.max() for f in histories.values() if not f.empty)
    end = end or str(pd.Timestamp(global_end).date())
    dates = _monthly_signal_dates(histories, start, end)
    eligible_histories = {k: v for k, v in histories.items() if k in allowed}
    benchmark_proxy = build_equal_weight_market_proxy(eligible_histories)
    max_positions = int(g2_cfg["quantitative_core"]["top_n"])
    max_per_peer = int(g2_cfg["quantitative_core"].get("max_selected_per_peer_group", 1))

    trades: list[Grok2Trade] = []
    active: list[dict] = []
    audit_rows: list[dict] = []

    for as_of in dates:
        universe = _research_universe_as_of(histories, allowed, as_of)
        if len(universe) < 3:
            audit_rows.append({"signal_date": str(as_of.date()), "universe": len(universe), "eligible": 0, "opened": 0, "status": "INSUFFICIENT_UNIVERSE"})
            continue
        snapshot, summary = score_grok2(universe, ref, base_cfg, g2_cfg)
        candidates = snapshot.loc[snapshot["grok2_decision"].isin(["ELIGIBLE", "BUY_CANDIDATE"])].sort_values("grok2_score_final", ascending=False)
        opened = 0
        blocked_capacity = 0
        blocked_peer_overlap = 0
        for row in candidates.itertuples(index=False):
            isin = str(row.instrument_id)
            next_obs = _next_row(histories[isin], as_of)
            if next_obs is None:
                continue
            entry_date, entry_price = next_obs
            active = [x for x in active if x["exit_date"] >= entry_date]
            if len(active) >= max_positions:
                blocked_capacity += 1
                continue
            if any(x["isin"] == isin for x in active):
                continue
            peer_group = str(row.grok2_peer_group)
            if sum(1 for x in active if x["peer_group"] == peer_group) >= max_per_peer:
                blocked_peer_overlap += 1
                continue
            exit_obs = _simulate_grok2_exit(histories[isin], entry_date, entry_price, g2_cfg)
            if exit_obs is None:
                continue
            exit_date, exit_price, hold, reason = exit_obs
            gross = exit_price / entry_price - 1.0
            net = _net_return(gross)
            bench = _benchmark_return(benchmark_proxy, entry_date, exit_date)
            excess = None if bench is None else float(net - bench)
            trades.append(Grok2Trade(
                isin=isin,
                peer_group=peer_group,
                signal_date=str(as_of.date()),
                entry_date=str(entry_date.date()),
                exit_date=str(exit_date.date()),
                entry_price=entry_price,
                exit_price=exit_price,
                gross_return=float(gross),
                net_return=net,
                benchmark_return=bench,
                excess_return=excess,
                holding_sessions=int(hold),
                exit_reason=reason,
                score_final=float(row.grok2_score_final),
            ))
            active.append({"isin": isin, "peer_group": peer_group, "entry_date": entry_date, "exit_date": exit_date})
            opened += 1

        audit_rows.append({
            "signal_date": str(as_of.date()),
            "universe": len(universe),
            "eligible": int(len(candidates)),
            "opened": opened,
            "blocked_capacity": blocked_capacity,
            "blocked_peer_overlap": blocked_peer_overlap,
            "regime_allowed": bool(summary.get("regime", {}).get("allowed", False)),
            "status": "OK",
        })

    out = root / "outputs" / "etf_grok2_research_backtest"
    out.mkdir(parents=True, exist_ok=True)
    tdf = pd.DataFrame([asdict(t) for t in trades])
    adf = pd.DataFrame(audit_rows)
    tdf.to_csv(out / "ETF_GROK2_TRADES.csv", index=False)
    adf.to_csv(out / "ETF_GROK2_SIGNAL_AUDIT.csv", index=False)
    overall = _stats(tdf)
    development = _segment_stats(tdf, "2013-01-01", "2020-12-31")
    validation = _segment_stats(tdf, "2021-01-01", "2023-12-31")
    diagnostic = _segment_stats(tdf, "2024-01-01", end)

    comparison = {
        "win_rate_delta_vs_v1": None if overall.get("win_rate") is None else float(overall["win_rate"] - frozen_v1["win_rate"]),
        "expectancy_delta_vs_v1": None if overall.get("expectancy") is None else float(overall["expectancy"] - frozen_v1["expectancy"]),
        "profit_factor_delta_vs_v1": None if overall.get("profit_factor") is None else float(overall["profit_factor"] - frozen_v1["profit_factor"]),
        "mean_excess_delta_vs_v1": None if overall.get("mean_excess_return") is None else float(overall["mean_excess_return"] - frozen_v1["mean_excess_return"]),
        "max_trade_loss_delta_vs_v1": None if overall.get("max_trade_loss") is None else float(overall["max_trade_loss"] - frozen_v1["max_trade_loss"]),
    }
    result = {
        "version": g2_cfg["version"],
        "status": "RESEARCH_CHALLENGER",
        "data_basis": "ETF_BACKTEST_BASE_V1_CURRENT_UNIVERSE_RECONSTRUCTION",
        "pit_price_features": True,
        "survivorship_bias_resolved": False,
        "promotion_eligible": False,
        "static_2026_fields_used_in_historical_score": False,
        "start": start,
        "end": end,
        "signal_dates": len(dates),
        "quality_eligible_instruments": len(allowed),
        "overall": overall,
        "development_2013_2020": development,
        "validation_2021_2023": validation,
        "diagnostic_2024_2026": diagnostic,
        "frozen_grok_v1": frozen_v1,
        "comparison_vs_v1": comparison,
    }
    (out / "ETF_GROK2_SUMMARY.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2013-01-01")
    p.add_argument("--end", default=None)
    args = p.parse_args(list(argv) if argv is not None else None)
    run(start=args.start, end=args.end)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
