from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from v182.backtest.etf_grok_research_backtest import _load_histories, _monthly_signal_dates, _quality_eligible, _research_universe_as_of
from v182.backtest.etf_grok2_exit_rotation_research import Position, ClosedTrade, _history_metrics, _reversal, _score_map, _stats, _world_benchmark
from v182.features.etf_grok2_cdc import score_grok2
from v182.io.frames import load_master

ROOT = Path(__file__).resolve().parents[3]
WORLD_ISIN = "LU1681043599"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _next_obs(frame: pd.DataFrame, d: pd.Timestamp, *, on_or_after: bool = False) -> tuple[pd.Timestamp, float] | None:
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna().sort_index()
    path = close.loc[close.index >= d] if on_or_after else close.loc[close.index > d]
    if path.empty:
        return None
    return pd.Timestamp(path.index[0]), float(path.iloc[0])


def simulate_variant(variant: str, histories: dict[str, pd.DataFrame], allowed: set[str], ref: pd.DataFrame, base_cfg: dict, g2_cfg: dict, cfg: dict, start: str, end: str):
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    initial = float(cfg["portfolio"]["initial_capital_eur"])
    entry_fee = float(cfg["portfolio"]["entry_fee_bps"]) / 10000.0
    exit_fee = float(cfg["portfolio"]["exit_fee_bps"]) / 10000.0
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
    pending_exits: dict[str, dict] = {}
    pending_entries: list[dict] = []
    cash = initial
    turnover = 0.0
    trades: list[ClosedTrade] = []
    equity_rows: list[dict] = []
    invested_obs: list[float] = []
    latest_scores: dict[str, dict] = {}

    def px_at(isin: str, d: pd.Timestamp) -> float | None:
        frame = histories[isin]
        if d not in frame.index:
            return None
        x = pd.to_numeric(pd.Series([frame.loc[d, "Close"]]), errors="coerce").iloc[0]
        return None if pd.isna(x) else float(x)

    def mark_px(isin: str, d: pd.Timestamp) -> float | None:
        close = pd.to_numeric(histories[isin]["Close"], errors="coerce").dropna().sort_index()
        close = close.loc[close.index <= d]
        return None if close.empty else float(close.iloc[-1])

    def equity_at(d: pd.Timestamp) -> tuple[float, float]:
        invested = 0.0
        for isin, p in positions.items():
            px = mark_px(isin, d)
            if px is not None:
                invested += p.units * px
        return cash + invested, invested

    def schedule_exit(isin: str, signal_date: pd.Timestamp, reason: str, score: float | None) -> pd.Timestamp | None:
        if isin in pending_exits or isin not in positions:
            return None
        nxt = _next_obs(histories[isin], signal_date)
        if nxt is None:
            return None
        pending_exits[isin] = {"exec_date": nxt[0], "reason": reason, "score": score}
        return nxt[0]

    def schedule_entry(isin: str, signal_date: pd.Timestamp, score: float, peer: str, earliest: pd.Timestamp | None = None):
        if isin in positions or any(x["isin"] == isin for x in pending_entries):
            return
        anchor = signal_date if earliest is None else max(signal_date, earliest - pd.Timedelta(nanoseconds=1))
        nxt = _next_obs(histories[isin], anchor)
        if nxt is None:
            return
        pending_entries.append({"isin": isin, "exec_date": nxt[0], "score": score, "peer": peer})

    def execute_exit(isin: str, d: pd.Timestamp, meta: dict):
        nonlocal cash, turnover
        if isin not in positions:
            return
        px = px_at(isin, d)
        if px is None:
            return
        p = positions[isin]
        gross = p.units * px
        cash += gross * (1.0 - exit_fee)
        turnover += gross
        net_ret = (1.0 - entry_fee) * (px / p.entry_price) * (1.0 - exit_fee) - 1.0
        trades.append(ClosedTrade(variant, isin, str(p.entry_date.date()), str(d.date()), p.entry_price, px, p.entry_score, meta.get("score"), float(net_ret), p.holding_sessions, meta["reason"]))
        del positions[isin]

    def execute_entry(order: dict, d: pd.Timestamp):
        nonlocal cash, turnover
        isin = order["isin"]
        if isin in positions or len(positions) >= max_pos:
            return
        px = px_at(isin, d)
        if px is None:
            return
        eq, _ = equity_at(d)
        budget = min(cash, eq * weight)
        if budget <= 0:
            return
        gross = budget / (1.0 + entry_fee)
        units = gross / px
        cash -= gross * (1.0 + entry_fee)
        turnover += gross
        positions[isin] = Position(isin, units, d, px, float(order["score"]), str(order["peer"]), px, 0)

    for d in dates:
        # Orders generated from prior sessions are executed first at today's close.
        for isin, meta in list(pending_exits.items()):
            if meta["exec_date"] == d:
                execute_exit(isin, d, meta)
                pending_exits.pop(isin, None)
        todays = [x for x in pending_entries if x["exec_date"] == d]
        pending_entries = [x for x in pending_entries if x["exec_date"] != d]
        for order in todays:
            execute_entry(order, d)

        # Update only when the ETF itself has a session today; signals never execute at this same close.
        for isin in list(positions):
            if d not in histories[isin].index or isin in pending_exits:
                continue
            p = positions[isin]
            m = _history_metrics(histories[isin], d)
            if m["close"] is None:
                continue
            p.holding_sessions += 1
            p.peak_price = max(p.peak_price, float(m["close"]))
            ret = float(m["close"] / p.entry_price - 1.0)
            if ret <= stop:
                schedule_exit(isin, d, "CATASTROPHIC_STOP", latest_scores.get(isin, {}).get("score"))
                continue
            if variant == "D_ROTATION_TRAIL_WIDE" and (p.peak_price / p.entry_price - 1.0) >= trail_arm and (float(m["close"]) / p.peak_price - 1.0) <= trail_dd:
                schedule_exit(isin, d, "TRAILING_PROTECTION", latest_scores.get(isin, {}).get("score"))

        if d in review_dates:
            universe = _research_universe_as_of(histories, allowed, d)
            if len(universe) >= 3:
                snap, _ = score_grok2(universe, ref, base_cfg, g2_cfg)
                latest_scores = _score_map(snap)
                ranked = sorted([(isin, v) for isin, v in latest_scores.items() if v["decision"] in {"ELIGIBLE", "BUY_CANDIDATE"}], key=lambda x: x[1]["score"], reverse=True)

                # Monthly strict reversal, all variants.
                for isin in list(positions):
                    if isin in pending_exits or positions[isin].holding_sessions < min_hold:
                        continue
                    m = _history_metrics(histories[isin], d)
                    if _reversal(m, min_rev):
                        schedule_exit(isin, d, "STRICT_MONTHLY_REVERSAL", latest_scores.get(isin, {}).get("score"))

                # Score exhaustion only for B/C/D and only with a material score break.
                if variant in {"B_REVERSAL_SCORE_STRICT", "C_ROTATION_STRICT", "D_ROTATION_TRAIL_WIDE"}:
                    for isin in list(positions):
                        if isin in pending_exits or positions[isin].holding_sessions < min_hold:
                            continue
                        cur = latest_scores.get(isin)
                        if cur is not None and (cur["score"] <= score_floor or positions[isin].entry_score - cur["score"] >= score_drop):
                            schedule_exit(isin, d, "STRICT_SCORE_EXHAUSTION", cur["score"])

                # Rotation is a high-hurdle replacement, not routine churning.
                if variant in {"C_ROTATION_STRICT", "D_ROTATION_TRAIL_WIDE"}:
                    for cand, cv in ranked:
                        if cand in positions or any(x["isin"] == cand for x in pending_entries):
                            continue
                        cm = _history_metrics(histories[cand], d)
                        if cm["perf63"] is None:
                            continue
                        best = None
                        for held, hp in positions.items():
                            if held in pending_exits or hp.holding_sessions < min_hold:
                                continue
                            hv = latest_scores.get(held)
                            hm = _history_metrics(histories[held], d)
                            if hv is None or hm["perf63"] is None:
                                continue
                            advantage = cv["score"] - hv["score"]
                            perf_adv = cm["perf63"] - hm["perf63"]
                            if advantage >= rot_score and perf_adv >= rot_perf:
                                rank = advantage + perf_adv * 100.0
                                if best is None or rank > best[0]:
                                    best = (rank, held)
                        if best is not None:
                            held = best[1]
                            exit_date = schedule_exit(held, d, "ROTATION_TO_MATERIALLY_STRONGER_ETF", latest_scores.get(held, {}).get("score"))
                            if exit_date is not None:
                                schedule_entry(cand, d, cv["score"], cv["peer_group"], earliest=exit_date)
                            break

                # Fill projected vacancies with top candidates; all new entries are next-session closes.
                projected_held = {x for x in positions if x not in pending_exits}
                projected_held.update(x["isin"] for x in pending_entries)
                for cand, cv in ranked:
                    if len(projected_held) >= max_pos:
                        break
                    if cand in projected_held or cand in positions:
                        continue
                    schedule_entry(cand, d, cv["score"], cv["peer_group"])
                    projected_held.add(cand)

        eq, invested = equity_at(d)
        equity_rows.append({"date": d, "equity": eq, "cash": cash, "invested": invested})
        invested_obs.append(0.0 if eq <= 0 else invested / eq)

    for isin, p in positions.items():
        px = mark_px(isin, end_ts)
        if px is None:
            continue
        net_ret = (1.0 - entry_fee) * (px / p.entry_price) - 1.0
        trades.append(ClosedTrade(variant, isin, str(p.entry_date.date()), str(end_ts.date()), p.entry_price, px, p.entry_score, latest_scores.get(isin, {}).get("score"), float(net_ret), p.holding_sessions, "END_OF_DATA"))

    edf = pd.DataFrame(equity_rows)
    tdf = pd.DataFrame([asdict(x) for x in trades])
    return _stats(edf, tdf, initial, invested_obs, turnover), tdf, edf


def run(root: Path = ROOT, start: str = "2013-01-01", end: str | None = None) -> dict:
    base_cfg = _json(root / "config/V20.8_ETF_GROK_HIGH_PRECISION.json")
    g2_cfg = _json(root / "config/ETF_GROK2_CDC_V1.json")
    cfg = _json(root / "config/ETF_GROK2_EXIT_RESEARCH_V2.json")
    histories = _load_histories(root)
    allowed = set(_quality_eligible(root))
    ref = load_master(root / "inputs/V18.2_PEA_ETF_MASTER.csv")
    end = end or str(max(f.index.max() for f in histories.values() if not f.empty).date())
    initial = float(cfg["portfolio"]["initial_capital_eur"])
    fee = float(cfg["portfolio"]["entry_fee_bps"]) / 10000.0
    world = _world_benchmark(histories, pd.Timestamp(start), pd.Timestamp(end), initial, fee)
    outdir = root / "outputs/etf_grok2_exit_rotation_research_v2"
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
        "execution": cfg["execution"], "world_benchmark": world, "variants": variants,
        "ranking_by_cagr_vs_world": ranked, "best_variant": ranked[0],
        "same_close_signal_execution_used": False, "fixed_take_profit_used": False,
        "survivorship_bias_resolved": False, "promotion_eligible": False, "real_orders_allowed": False
    }
    (outdir / "SUMMARY.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
