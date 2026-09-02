from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from v182.features.etf_grok_v2081 import score_snapshot
from v182.io.frames import load_master

ROOT = Path(__file__).resolve().parents[3]
ROUND_TRIP_COST = 0.005  # 25 bp each side, consistent with historical replay documentation.


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
        rename = {c: str(c).title().replace("_", " ") for c in frame.columns}
        frame = frame.rename(columns=rename)
        if "Adj Close" not in frame.columns and "Adj Close" in rename.values():
            pass
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
    s = pd.Series(idx, index=idx)
    return [pd.Timestamp(x) for x in s.groupby(idx.to_period("M")).max().tolist()]


def _next_row(frame: pd.DataFrame, after: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    future = frame.loc[frame.index > after]
    if future.empty or "Close" not in future.columns:
        return None
    close = pd.to_numeric(future["Close"], errors="coerce").dropna()
    if close.empty:
        return None
    d = close.index[0]
    return pd.Timestamp(d), float(close.iloc[0])


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
    d = pd.Timestamp(path.index[-1])
    return d, float(path.iloc[-1]), max(0, len(path) - 1), "END_OF_DATA"


def _stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0, "wins": 0, "win_rate": None, "expectancy": None, "profit_factor": None, "mean_net_return": None, "median_net_return": None, "max_trade_loss": None}
    r = pd.to_numeric(trades["net_return"], errors="coerce").dropna()
    wins = r[r > 0]
    losses = r[r <= 0]
    loss_abs = abs(float(losses.sum()))
    return {
        "trades": int(len(r)),
        "wins": int((r > 0).sum()),
        "win_rate": float((r > 0).mean()),
        "expectancy": float(r.mean()),
        "profit_factor": None if loss_abs == 0 else float(wins.sum() / loss_abs),
        "mean_net_return": float(r.mean()),
        "median_net_return": float(r.median()),
        "max_trade_loss": float(r.min()),
    }


def run(root: Path = ROOT, start: str = "2013-01-01", end: str | None = None) -> dict:
    cfg = _load_config(root)
    histories = _load_histories(root)
    allowed = _quality_eligible(root)
    ref = load_master(root / "inputs" / "V18.2_PEA_ETF_MASTER.csv")
    global_end = max(f.index.max() for f in histories.values() if not f.empty)
    end = end or str(pd.Timestamp(global_end).date())
    signal_dates = _monthly_signal_dates(histories, start, end)
    trades: list[Trade] = []
    audit_rows: list[dict] = []

    for as_of in signal_dates:
        universe = _research_universe_as_of(histories, allowed, as_of)
        if len(universe) < 3:
            audit_rows.append({"signal_date": str(as_of.date()), "universe": len(universe), "selected": 0, "status": "INSUFFICIENT_UNIVERSE"})
            continue
        snapshot, summary = score_snapshot(universe, ref, cfg)
        selected = snapshot.loc[snapshot.get("selected", False).astype(bool)] if "selected" in snapshot.columns else snapshot.iloc[0:0]
        audit_rows.append({
            "signal_date": str(as_of.date()), "universe": len(universe), "selected": int(len(selected)),
            "regime_allowed": bool(summary.get("regime", {}).get("allowed", False)),
            "status": "OK",
        })
        for row in selected.itertuples(index=False):
            isin = str(row.instrument_id)
            next_obs = _next_row(histories[isin], as_of)
            if next_obs is None:
                continue
            entry_date, entry_price = next_obs
            exit_obs = _simulate_exit(histories[isin], entry_date, entry_price, cfg)
            if exit_obs is None:
                continue
            exit_date, exit_price, hold, reason = exit_obs
            gross = exit_price / entry_price - 1.0
            trades.append(Trade(
                isin=isin,
                signal_date=str(as_of.date()),
                entry_date=str(entry_date.date()),
                exit_date=str(exit_date.date()),
                entry_price=entry_price,
                exit_price=exit_price,
                gross_return=float(gross),
                net_return=float(gross - ROUND_TRIP_COST),
                holding_sessions=int(hold),
                exit_reason=reason,
                score_final=float(getattr(row, "score_final")),
            ))

    out = root / "outputs" / "etf_grok_research_backtest"
    out.mkdir(parents=True, exist_ok=True)
    trades_df = pd.DataFrame([asdict(t) for t in trades])
    audit_df = pd.DataFrame(audit_rows)
    trades_df.to_csv(out / "ETF_GROK_V1_TRADES.csv", index=False)
    audit_df.to_csv(out / "ETF_GROK_V1_SIGNAL_AUDIT.csv", index=False)
    stats = _stats(trades_df)
    result = {
        "version": "ETF_GROK_V1_OPERATIONAL_RESEARCH_BACKTEST_2026_09_03",
        "model": cfg["version"],
        "data_basis": "ETF_BACKTEST_BASE_V1_CURRENT_UNIVERSE_RECONSTRUCTION",
        "pit_price_features": True,
        "pit_membership_complete": False,
        "survivorship_bias_resolved": False,
        "promotion_eligible": False,
        "entry_execution": "NEXT_SESSION_CLOSE",
        "round_trip_cost": ROUND_TRIP_COST,
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
