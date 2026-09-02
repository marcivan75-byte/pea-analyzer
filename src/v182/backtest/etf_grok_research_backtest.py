from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from v182.features.etf_grok_v2081 import build_equal_weight_market_proxy, score_snapshot
from v182.io.frames import load_master

ROOT = Path(__file__).resolve().parents[3]
COST_PER_SIDE = 0.0025


@dataclass
class Trade:
    isin: str
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


def _load_config(root: Path) -> dict:
    return json.loads((root / "config" / "V20.8_ETF_GROK_HIGH_PRECISION.json").read_text(encoding="utf-8"))


def _load_histories(root: Path) -> dict[str, pd.DataFrame]:
    prices_dir = root / "data" / "backtest" / "etf_base_v1" / "prices"
    if not prices_dir.exists():
        raise RuntimeError("ETF_GROK_BACKTEST_REQUIRES_ETF_BASE_V1_PRICES")
    histories: dict[str, pd.DataFrame] = {}
    for path in sorted(prices_dir.glob("*.parquet")):
        frame = pd.read_parquet(path)
        if frame.empty or "date" not in frame.columns:
            continue
        frame = frame.rename(columns={c: str(c).title().replace("_", " ") for c in frame.columns})
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
        frame = frame.dropna(subset=["Date"]).drop_duplicates("Date", keep="last").sort_values("Date").set_index("Date")
        histories[path.stem] = frame
    if not histories:
        raise RuntimeError("ETF_GROK_BACKTEST_NO_HISTORIES")
    return histories


def _quality_eligible(root: Path) -> set[str]:
    path = root / "data" / "backtest" / "etf_base_v1" / "ETF_BACKTEST_INSTRUMENT_QUALITY.csv"
    q = pd.read_csv(path, sep=";", dtype=str, keep_default_na=False)
    flag = q["mt_close_only_ready"].astype(str).str.lower().isin({"true", "1"})
    return set(q.loc[flag, "isin"].astype(str))


def _research_universe_as_of(histories: dict[str, pd.DataFrame], allowed: set[str], as_of: pd.Timestamp) -> dict[str, pd.DataFrame]:
    # Diagnostic current-universe reconstruction: existence and quality gated, never promotion eligible.
    result: dict[str, pd.DataFrame] = {}
    for isin, frame in histories.items():
        if isin not in allowed:
            continue
        truncated = frame.loc[frame.index <= as_of]
        if len(truncated) >= 757:
            result[isin] = truncated
    return result


def _monthly_signal_dates(histories: dict[str, pd.DataFrame], start: str, end: str) -> list[pd.Timestamp]:
    all_dates = sorted(set().union(*(set(f.index) for f in histories.values() if not f.empty)))
    idx = pd.DatetimeIndex(all_dates)
    idx = idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]
    if idx.empty:
        return []
    return [pd.Timestamp(x) for x in pd.Series(idx, index=idx).groupby(idx.to_period("M")).max().tolist()]


def _next_row(frame: pd.DataFrame, after: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    future = frame.loc[frame.index > after]
    if future.empty or "Close" not in future.columns:
        return None
    close = pd.to_numeric(future["Close"], errors="coerce").dropna()
    if close.empty:
        return None
    return pd.Timestamp(close.index[0]), float(close.iloc[0])


def _simulate_exit(frame: pd.DataFrame, entry_date: pd.Timestamp, entry_price: float, cfg: dict) -> tuple[pd.Timestamp, float, int, str] | None:
    policy = cfg["exit_policy"]
    target = float(policy["target_return"])
    stop = float(policy["hard_stop_return"])
    max_hold = int(policy["max_holding_sessions"])
    path = pd.to_numeric(frame.loc[frame.index >= entry_date, "Close"], errors="coerce").dropna()
    if path.empty:
        return None
    for i, (d, px) in enumerate(path.items()):
        px = float(px)
        ret = px / entry_price - 1.0
        if ret >= target:
            return pd.Timestamp(d), px, i, "TARGET_CLOSE"
        if ret <= stop:
            return pd.Timestamp(d), px, i, "STOP_CLOSE"
        if i >= max_hold:
            return pd.Timestamp(d), px, i, "TIME_CLOSE"
    return pd.Timestamp(path.index[-1]), float(path.iloc[-1]), max(0, len(path) - 1), "END_OF_DATA"


def _net_return(gross: float) -> float:
    # Buy pays 25 bp; sale pays 25 bp. Multiplicative treatment avoids a hidden linear approximation.
    return float((1.0 - COST_PER_SIDE) * (1.0 + gross) * (1.0 - COST_PER_SIDE) - 1.0)


def _benchmark_return(proxy: pd.Series, entry_date: pd.Timestamp, exit_date: pd.Timestamp) -> float | None:
    p = proxy.loc[(proxy.index >= entry_date) & (proxy.index <= exit_date)].dropna()
    if len(p) < 2 or float(p.iloc[0]) <= 0:
        return None
    return float(p.iloc[-1] / p.iloc[0] - 1.0)


def _stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades_total": 0, "closed_trades": 0, "open_end_of_data": 0, "wins": 0, "win_rate": None, "expectancy": None, "profit_factor": None, "mean_net_return": None, "median_net_return": None, "max_trade_loss": None, "mean_excess_return": None}
    closed = trades[trades["exit_reason"] != "END_OF_DATA"].copy()
    r = pd.to_numeric(closed["net_return"], errors="coerce").dropna()
    wins = r[r > 0]
    losses = r[r <= 0]
    loss_abs = abs(float(losses.sum()))
    excess = pd.to_numeric(closed.get("excess_return"), errors="coerce").dropna() if "excess_return" in closed else pd.Series(dtype=float)
    return {
        "trades_total": int(len(trades)),
        "closed_trades": int(len(r)),
        "open_end_of_data": int((trades["exit_reason"] == "END_OF_DATA").sum()),
        "wins": int((r > 0).sum()),
        "win_rate": None if r.empty else float((r > 0).mean()),
        "expectancy": None if r.empty else float(r.mean()),
        "profit_factor": None if r.empty or loss_abs == 0 else float(wins.sum() / loss_abs),
        "mean_net_return": None if r.empty else float(r.mean()),
        "median_net_return": None if r.empty else float(r.median()),
        "max_trade_loss": None if r.empty else float(r.min()),
        "mean_excess_return": None if excess.empty else float(excess.mean()),
    }


def run(root: Path = ROOT, start: str = "2013-01-01", end: str | None = None) -> dict:
    cfg = _load_config(root)
    histories = _load_histories(root)
    allowed = _quality_eligible(root)
    ref = load_master(root / "inputs" / "V18.2_PEA_ETF_MASTER.csv")
    global_end = max(f.index.max() for f in histories.values() if not f.empty)
    end = end or str(pd.Timestamp(global_end).date())
    signal_dates = _monthly_signal_dates(histories, start, end)
    eligible_histories = {k: v for k, v in histories.items() if k in allowed}
    benchmark_proxy = build_equal_weight_market_proxy(eligible_histories)
    max_positions = int(cfg["score"]["top_n"])

    trades: list[Trade] = []
    active: list[dict] = []
    audit_rows: list[dict] = []

    for as_of in signal_dates:
        universe = _research_universe_as_of(histories, allowed, as_of)
        if len(universe) < 3:
            audit_rows.append({"signal_date": str(as_of.date()), "universe": len(universe), "eligible_candidates": 0, "opened": 0, "active_before": len(active), "status": "INSUFFICIENT_UNIVERSE"})
            continue
        snapshot, summary = score_snapshot(universe, ref, cfg)
        threshold = float(cfg["score"]["selection_threshold"])
        candidates = snapshot.loc[
            snapshot["criteria_complete"].astype(bool)
            & snapshot["regime_allowed"].astype(bool)
            & (pd.to_numeric(snapshot["score_final"], errors="coerce") >= threshold)
        ].sort_values("score_final", ascending=False)

        opened = 0
        active_before = len([x for x in active if x["exit_date"] > as_of])
        for row in candidates.itertuples(index=False):
            isin = str(row.instrument_id)
            next_obs = _next_row(histories[isin], as_of)
            if next_obs is None:
                continue
            entry_date, entry_price = next_obs
            # Remove positions already closed before the contemplated entry.
            active = [x for x in active if x["exit_date"] >= entry_date]
            if len(active) >= max_positions:
                break
            if any(x["isin"] == isin for x in active):
                continue
            exit_obs = _simulate_exit(histories[isin], entry_date, entry_price, cfg)
            if exit_obs is None:
                continue
            exit_date, exit_price, hold, reason = exit_obs
            gross = exit_price / entry_price - 1.0
            net = _net_return(gross)
            bench = _benchmark_return(benchmark_proxy, entry_date, exit_date)
            excess = None if bench is None else float(net - bench)
            trade = Trade(
                isin=isin,
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
                score_final=float(getattr(row, "score_final")),
            )
            trades.append(trade)
            active.append({"isin": isin, "entry_date": entry_date, "exit_date": exit_date})
            opened += 1

        audit_rows.append({
            "signal_date": str(as_of.date()),
            "universe": len(universe),
            "eligible_candidates": int(len(candidates)),
            "opened": opened,
            "active_before": active_before,
            "active_after": len([x for x in active if x["exit_date"] > as_of]),
            "regime_allowed": bool(summary.get("regime", {}).get("allowed", False)),
            "status": "OK",
        })

    out = root / "outputs" / "etf_grok_research_backtest"
    out.mkdir(parents=True, exist_ok=True)
    trades_df = pd.DataFrame([asdict(t) for t in trades])
    audit_df = pd.DataFrame(audit_rows)
    trades_df.to_csv(out / "ETF_GROK_V1_TRADES.csv", index=False)
    audit_df.to_csv(out / "ETF_GROK_V1_SIGNAL_AUDIT.csv", index=False)
    stats = _stats(trades_df)

    # Operational integrity: one instrument cannot be held twice simultaneously and portfolio capacity is bounded.
    overlap_violations = 0
    if not trades_df.empty:
        temp = trades_df.copy()
        temp["entry_date"] = pd.to_datetime(temp["entry_date"])
        temp["exit_date"] = pd.to_datetime(temp["exit_date"])
        for _, group in temp.sort_values("entry_date").groupby("isin"):
            previous_exit = None
            for _, row in group.iterrows():
                if previous_exit is not None and row["entry_date"] <= previous_exit:
                    overlap_violations += 1
                previous_exit = row["exit_date"] if previous_exit is None else max(previous_exit, row["exit_date"])

    result = {
        "version": "ETF_GROK_V1_OPERATIONAL_RESEARCH_BACKTEST_2026_09_03_R2",
        "model": cfg["version"],
        "data_basis": "ETF_BACKTEST_BASE_V1_CURRENT_UNIVERSE_RECONSTRUCTION",
        "pit_price_features": True,
        "pit_membership_complete": False,
        "survivorship_bias_resolved": False,
        "promotion_eligible": False,
        "entry_execution": "NEXT_SESSION_CLOSE",
        "cost_per_side": COST_PER_SIDE,
        "portfolio_max_positions": max_positions,
        "duplicate_position_overlap_violations": overlap_violations,
        "start": start,
        "end": end,
        "signal_dates": len(signal_dates),
        "max_quality_eligible_instruments": len(allowed),
        **stats,
    }
    (out / "ETF_GROK_V1_SUMMARY.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
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
