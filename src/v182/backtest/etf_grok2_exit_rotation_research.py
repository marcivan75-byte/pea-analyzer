from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

from v182.backtest.etf_grok_research_backtest import (
    _load_histories, _monthly_signal_dates, _quality_eligible, _research_universe_as_of
)
from v182.features.etf_grok2_cdc import score_grok2
from v182.io.frames import load_master

ROOT = Path(__file__).resolve().parents[3]
WORLD_ISIN = "LU1681043599"


@dataclass
class Position:
    isin: str
    units: float
    entry_date: pd.Timestamp
    entry_price: float
    entry_score: float
    peer_group: str
    peak_price: float
    holding_sessions: int = 0


@dataclass
class ClosedTrade:
    variant: str
    isin: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    entry_score: float
    exit_score: float | None
    net_return: float
    holding_sessions: int
    exit_reason: str


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _close_asof(frame: pd.DataFrame, d: pd.Timestamp) -> float | None:
    s = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    s = s.loc[s.index <= d]
    return None if s.empty else float(s.iloc[-1])


def _history_metrics(frame: pd.DataFrame, d: pd.Timestamp) -> dict:
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna().sort_index()
    close = close.loc[close.index <= d]
    if close.empty:
        return {"close": None, "sma50": None, "macd_hist": None, "perf20": None, "perf63": None}
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return {
        "close": float(close.iloc[-1]),
        "sma50": None if len(close) < 50 else float(close.rolling(50).mean().iloc[-1]),
        "macd_hist": float((macd - signal).iloc[-1]),
        "perf20": None if len(close) < 21 else float(close.iloc[-1] / close.iloc[-21] - 1.0),
        "perf63": None if len(close) < 64 else float(close.iloc[-1] / close.iloc[-64] - 1.0),
    }


def _reversal(m: dict, minimum_confirmations: int) -> bool:
    checks = [
        m["sma50"] is not None and m["close"] < m["sma50"],
        m["macd_hist"] is not None and m["macd_hist"] < 0.0,
        m["perf20"] is not None and m["perf20"] < 0.0,
    ]
    return sum(bool(x) for x in checks) >= minimum_confirmations


def _score_map(snapshot: pd.DataFrame) -> dict[str, dict]:
    out = {}
    for r in snapshot.itertuples(index=False):
        score = getattr(r, "grok2_score_final", np.nan)
        if pd.isna(score):
            continue
        out[str(r.instrument_id)] = {
            "score": float(score),
            "peer_group": str(getattr(r, "grok2_peer_group", "UNCLASSIFIED")),
            "decision": str(getattr(r, "grok2_decision", "")),
        }
    return out


def _stats(equity: pd.DataFrame, trades: pd.DataFrame, initial: float, invested_obs: list[float], turnover_eur: float) -> dict:
    if equity.empty:
        return {}
    e = equity.set_index("date")["equity"].astype(float)
    daily = e.pct_change().dropna()
    years = max((e.index[-1] - e.index[0]).days / 365.25, 1 / 365.25)
    cagr = float((e.iloc[-1] / initial) ** (1.0 / years) - 1.0)
    peak = e.cummax()
    dd = e / peak - 1.0
    max_dd = float(dd.min())
    neg = daily[daily < 0]
    downside = float(neg.std(ddof=0)) if len(neg) else 0.0
    sortino = None if downside <= 0 else float(daily.mean() / downside * math.sqrt(252))
    calmar = None if max_dd >= 0 else float(cagr / abs(max_dd))
    closed = trades.loc[trades["exit_reason"] != "END_OF_DATA"] if not trades.empty else trades
    win_rate = None if closed.empty else float((closed["net_return"] > 0).mean())
    annual = e.resample("YE").last().pct_change()
    if len(annual):
        first_year = annual.index[0]
        first_slice = e.loc[e.index.year == first_year.year]
        annual.iloc[0] = float(first_slice.iloc[-1] / initial - 1.0)
    annual_returns = {str(idx.year): float(v) for idx, v in annual.dropna().items()}
    return {
        "final_equity": float(e.iloc[-1]), "total_return": float(e.iloc[-1] / initial - 1.0),
        "cagr": cagr, "max_drawdown": max_dd, "sortino": sortino, "calmar": calmar,
        "closed_trades": int(len(closed)), "win_rate": win_rate,
        "turnover_multiple_initial_capital": float(turnover_eur / initial),
        "capital_utilisation": float(np.mean(invested_obs)) if invested_obs else 0.0,
        "annual_returns": annual_returns,
    }


def _world_benchmark(histories: dict[str, pd.DataFrame], start: pd.Timestamp, end: pd.Timestamp, initial: float, fee: float) -> dict:
    frame = histories[WORLD_ISIN]
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna().sort_index()
    close = close.loc[(close.index >= start) & (close.index <= end)]
    if close.empty:
        raise RuntimeError("WORLD_BENCHMARK_HISTORY_MISSING")
    units = initial * (1.0 - fee) / float(close.iloc[0])
    equity = units * close
    final = float(equity.iloc[-1] * (1.0 - fee))
    e = equity.copy(); e.iloc[-1] = final
    years = max((e.index[-1] - e.index[0]).days / 365.25, 1 / 365.25)
    peak = e.cummax(); dd = e / peak - 1.0
    cagr = float((final / initial) ** (1 / years) - 1)
    annual = e.resample("YE").last().pct_change()
    first_slice = e.loc[e.index.year == e.index[0].year]
    if len(annual): annual.iloc[0] = float(first_slice.iloc[-1] / initial - 1.0)
    return {
        "isin": WORLD_ISIN, "final_equity": final, "total_return": final / initial - 1.0,
        "cagr": cagr, "max_drawdown": float(dd.min()),
        "annual_returns": {str(idx.year): float(v) for idx, v in annual.dropna().items()},
    }


def simulate_variant(variant: str, histories: dict[str, pd.DataFrame], allowed: set[str], ref: pd.DataFrame, base_cfg: dict, g2_cfg: dict, cfg: dict, start: str, end: str) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    initial = float(cfg["portfolio"]["initial_capital_eur"])
    fee = float(cfg["portfolio"]["entry_fee_bps"]) / 10000.0
    max_pos = int(cfg["portfolio"]["max_positions"])
    weight = float(cfg["portfolio"]["weight_per_position"])
    min_hold = int(cfg["review"]["minimum_holding_sessions"])
    stop = float(cfg["review"]["catastrophic_stop_return"])
    min_rev = int(cfg["review"]["reversal"]["minimum_confirmations"])
    score_drop = float(cfg["review"]["score_exhaustion"]["entry_score_drop_points"])
    score_floor = float(cfg["review"]["score_exhaustion"]["absolute_score_floor"])
    rot_score = float(cfg["review"]["rotation"]["minimum_score_advantage_points"])
    rot_perf = float(cfg["review"]["rotation"]["minimum_perf63_advantage_points"])
    trail_dd = float(cfg["review"]["trailing_protection"]["drawdown_from_peak"])
    trail_arm = float(cfg["review"]["trailing_protection"]["activate_after_gain"])

    dates = sorted(set().union(*[set(f.index[(f.index >= start_ts) & (f.index <= end_ts)]) for k, f in histories.items() if k in allowed]))
    review_dates = set(_monthly_signal_dates(histories, start, end))
    positions: dict[str, Position] = {}
    cash = initial
    turnover = 0.0
    trades: list[ClosedTrade] = []
    equity_rows, invested_obs = [], []
    latest_scores: dict[str, dict] = {}

    def equity_at(d: pd.Timestamp) -> tuple[float, float]:
        invested = 0.0
        for isin, p in positions.items():
            px = _close_asof(histories[isin], d)
            if px is not None: invested += p.units * px
        return cash + invested, invested

    def close_pos(isin: str, d: pd.Timestamp, reason: str, exit_score: float | None):
        nonlocal cash, turnover
        p = positions[isin]
        px = _close_asof(histories[isin], d)
        if px is None: return
        gross_proceeds = p.units * px
        proceeds = gross_proceeds * (1.0 - fee)
        cash += proceeds
        turnover += gross_proceeds
        net_ret = (1.0 - fee) * (px / p.entry_price) * (1.0 - fee) - 1.0
        trades.append(ClosedTrade(variant, isin, str(p.entry_date.date()), str(d.date()), p.entry_price, px, p.entry_score, exit_score, float(net_ret), p.holding_sessions, reason))
        del positions[isin]

    def open_pos(isin: str, d: pd.Timestamp, score: float, peer: str):
        nonlocal cash, turnover
        if isin in positions or len(positions) >= max_pos: return
        px = _close_asof(histories[isin], d)
        if px is None: return
        eq, _ = equity_at(d)
        budget = min(cash, eq * weight)
        if budget <= 0: return
        gross_budget = budget / (1.0 + fee)
        units = gross_budget / px
        fee_amt = gross_budget * fee
        cash -= gross_budget + fee_amt
        turnover += gross_budget
        positions[isin] = Position(isin, units, d, px, score, peer, px, 0)

    for d in dates:
        # Daily protective and reversal exits.
        for isin in list(positions):
            p = positions[isin]
            m = _history_metrics(histories[isin], d)
            if m["close"] is None: continue
            p.holding_sessions += 1
            p.peak_price = max(p.peak_price, float(m["close"]))
            ret = float(m["close"] / p.entry_price - 1.0)
            if ret <= stop:
                close_pos(isin, d, "CATASTROPHIC_STOP", latest_scores.get(isin, {}).get("score")); continue
            if variant == "D_ROTATION_TRAIL" and (p.peak_price / p.entry_price - 1.0) >= trail_arm and (float(m["close"]) / p.peak_price - 1.0) <= trail_dd:
                close_pos(isin, d, "TRAILING_PROTECTION", latest_scores.get(isin, {}).get("score")); continue
            if p.holding_sessions >= min_hold and _reversal(m, min_rev):
                close_pos(isin, d, "TECHNICAL_REVERSAL", latest_scores.get(isin, {}).get("score"))

        if d in review_dates:
            universe = _research_universe_as_of(histories, allowed, d)
            if len(universe) >= 3:
                snap, _ = score_grok2(universe, ref, base_cfg, g2_cfg)
                latest_scores = _score_map(snap)
                ranked = sorted(
                    [(isin, v) for isin, v in latest_scores.items() if v["decision"] in {"ELIGIBLE", "BUY_CANDIDATE"}],
                    key=lambda x: x[1]["score"], reverse=True
                )
                if variant in {"B_REVERSAL_SCORE", "C_REVERSAL_SCORE_ROTATION", "D_ROTATION_TRAIL"}:
                    for isin in list(positions):
                        if isin not in positions: continue
                        cur = latest_scores.get(isin)
                        if cur is None or cur["score"] <= score_floor or positions[isin].entry_score - cur["score"] >= score_drop:
                            close_pos(isin, d, "SCORE_EXHAUSTION", None if cur is None else cur["score"])
                if variant in {"C_REVERSAL_SCORE_ROTATION", "D_ROTATION_TRAIL"} and positions:
                    for cand, cv in ranked:
                        if cand in positions: continue
                        cm = _history_metrics(histories[cand], d)
                        if cm["perf63"] is None: continue
                        weakest = None
                        for held in positions:
                            hv = latest_scores.get(held)
                            if hv is None: continue
                            hm = _history_metrics(histories[held], d)
                            if hm["perf63"] is None: continue
                            advantage = cv["score"] - hv["score"]
                            perf_adv = cm["perf63"] - hm["perf63"]
                            if advantage >= rot_score and perf_adv >= rot_perf:
                                key = (advantage + perf_adv * 100.0, held)
                                if weakest is None or key > weakest[0]: weakest = (key, held)
                        if weakest:
                            held = weakest[1]
                            close_pos(held, d, "ROTATION_TO_STRONGER_ETF", latest_scores.get(held, {}).get("score"))
                            open_pos(cand, d, cv["score"], cv["peer_group"])
                            break
                for cand, cv in ranked:
                    if len(positions) >= max_pos: break
                    open_pos(cand, d, cv["score"], cv["peer_group"])

        eq, invested = equity_at(d)
        equity_rows.append({"date": d, "equity": eq, "cash": cash, "invested": invested})
        invested_obs.append(0.0 if eq <= 0 else invested / eq)

    # Mark open positions as END_OF_DATA for trade audit only; do not liquidate in equity.
    for isin, p in positions.items():
        px = _close_asof(histories[isin], end_ts)
        if px is None: continue
        net_ret = (1.0 - fee) * (px / p.entry_price) - 1.0
        trades.append(ClosedTrade(variant, isin, str(p.entry_date.date()), str(end_ts.date()), p.entry_price, px, p.entry_score, latest_scores.get(isin, {}).get("score"), float(net_ret), p.holding_sessions, "END_OF_DATA"))

    edf = pd.DataFrame(equity_rows)
    tdf = pd.DataFrame([asdict(x) for x in trades])
    stats = _stats(edf, tdf, initial, invested_obs, turnover)
    return stats, tdf, edf


def run(root: Path = ROOT, start: str = "2013-01-01", end: str | None = None) -> dict:
    base_cfg = _json(root / "config/V20.8_ETF_GROK_HIGH_PRECISION.json")
    g2_cfg = _json(root / "config/ETF_GROK2_CDC_V1.json")
    cfg = _json(root / "config/ETF_GROK2_EXIT_RESEARCH_V1.json")
    histories = _load_histories(root)
    allowed = set(_quality_eligible(root))
    ref = load_master(root / "inputs/V18.2_PEA_ETF_MASTER.csv")
    end = end or str(max(f.index.max() for f in histories.values() if not f.empty).date())
    initial = float(cfg["portfolio"]["initial_capital_eur"])
    fee = float(cfg["portfolio"]["entry_fee_bps"]) / 10000.0
    world = _world_benchmark(histories, pd.Timestamp(start), pd.Timestamp(end), initial, fee)

    outdir = root / "outputs/etf_grok2_exit_rotation_research"
    outdir.mkdir(parents=True, exist_ok=True)
    variants = {}
    for variant in cfg["variants"]:
        stats, trades, equity = simulate_variant(variant, histories, allowed, ref, base_cfg, g2_cfg, cfg, start, end)
        stats["cagr_delta_vs_world"] = float(stats["cagr"] - world["cagr"])
        stats["final_equity_delta_vs_world"] = float(stats["final_equity"] - world["final_equity"])
        variants[variant] = stats
        trades.to_csv(outdir / f"{variant}_TRADES.csv", index=False)
        equity.to_csv(outdir / f"{variant}_EQUITY.csv", index=False)

    ranked = sorted(variants, key=lambda k: (variants[k]["cagr_delta_vs_world"], variants[k]["calmar"] or -999), reverse=True)
    result = {
        "version": cfg["version"], "status": cfg["status"], "start": start, "end": end,
        "world_benchmark": world, "variants": variants, "ranking_by_cagr_vs_world": ranked,
        "best_variant": ranked[0], "take_profit_fixed_pct_used": False,
        "survivorship_bias_resolved": False, "promotion_eligible": False, "real_orders_allowed": False,
    }
    (outdir / "SUMMARY.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
