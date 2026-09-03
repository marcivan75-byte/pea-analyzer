from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from v182.backtest.etf_grok_research_backtest import _load_histories, _monthly_signal_dates, _quality_eligible, _research_universe_as_of
from v182.backtest.etf_grok2_exit_rotation_research import Position, ClosedTrade, _history_metrics, _reversal, _score_map, _stats, _world_benchmark
from v182.backtest.etf_grok2_exit_rotation_research_v2 import _next_obs, simulate_variant as simulate_v2
from v182.features.etf_grok2_cdc import score_grok2
from v182.io.frames import load_master

ROOT = Path(__file__).resolve().parents[3]
WORLD_ISIN = "LU1681043599"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _close(frame: pd.DataFrame, d: pd.Timestamp, exact: bool = False) -> float | None:
    s = pd.to_numeric(frame["Close"], errors="coerce").dropna().sort_index()
    if exact:
        if d not in s.index:
            return None
        return float(s.loc[d])
    s = s.loc[s.index <= d]
    return None if s.empty else float(s.iloc[-1])


def _relative_perf63(histories: dict[str, pd.DataFrame], isin: str, d: pd.Timestamp) -> float | None:
    a = _history_metrics(histories[isin], d).get("perf63")
    w = _history_metrics(histories[WORLD_ISIN], d).get("perf63")
    if a is None or w is None:
        return None
    return float(a - w)


def simulate_world_variant(variant: str, histories: dict[str, pd.DataFrame], allowed: set[str], ref: pd.DataFrame,
                           base_cfg: dict, g2_cfg: dict, cfg: dict, start: str, end: str):
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    initial = float(cfg["portfolio"]["initial_capital_eur"])
    entry_fee = float(cfg["portfolio"]["active_entry_fee_bps"]) / 10000.0
    exit_fee = float(cfg["portfolio"]["active_exit_fee_bps"]) / 10000.0
    fallback_fee = float(cfg["portfolio"].get("fallback_rebalance_fee_bps", 0)) / 10000.0
    max_pos = int(cfg["portfolio"]["max_grok_positions"])
    weight = float(cfg["portfolio"]["weight_per_grok_position"])
    if max_pos != 2 or abs(weight * max_pos - 1.0) > 1e-12:
        raise RuntimeError("V3_REQUIRES_TWO_FULLY_INVESTED_SLEEVES")
    min_hold = int(cfg["review"]["minimum_holding_sessions"])
    stop = float(cfg["review"]["catastrophic_stop_return"])
    min_rev = int(cfg["review"]["reversal"]["minimum_confirmations"])
    rot_score = float(cfg["review"]["rotation"]["minimum_score_advantage_points"])
    rot_perf = float(cfg["review"]["rotation"]["minimum_perf63_advantage_points"])
    rel_min_hold = int(cfg["review"]["relative_exit"]["minimum_holding_sessions"])
    rel_floor = float(cfg["review"]["relative_exit"]["underperformance_vs_world_63_points"])
    rel_score = float(cfg["review"]["relative_exit"]["maximum_score_to_exit"])

    dates = sorted(set().union(*[set(f.index[(f.index >= start_ts) & (f.index <= end_ts)]) for k, f in histories.items() if k in allowed or k == WORLD_ISIN]))
    if not dates:
        raise RuntimeError("V3_NO_DATES")
    first_world = _next_obs(histories[WORLD_ISIN], start_ts - pd.Timedelta(days=1))
    if first_world is None:
        raise RuntimeError("V3_WORLD_START_MISSING")

    # Two independent sleeves. Each sleeve is always invested either in World or one GROK ETF.
    # Its NAV is never resized to 50% of total portfolio after inception; it evolves independently.
    sleeve_world_units = {sid: initial * weight * (1.0 - fallback_fee) / first_world[1] for sid in range(max_pos)}
    sleeve_position: dict[int, Position | None] = {sid: None for sid in range(max_pos)}
    position_sleeve: dict[str, int] = {}
    pending_exits: dict[str, dict] = {}
    pending_entries: list[dict] = []
    trades: list[ClosedTrade] = []
    equity_rows: list[dict] = []
    active_utilisation: list[float] = []
    turnover = initial
    latest_scores: dict[str, dict] = {}
    review_dates = set(_monthly_signal_dates(histories, start, end))

    def world_px(d: pd.Timestamp) -> float:
        x = _close(histories[WORLD_ISIN], d)
        if x is None:
            raise RuntimeError("V3_WORLD_MARK_MISSING")
        return x

    def positions() -> dict[str, Position]:
        return {p.isin: p for p in sleeve_position.values() if p is not None}

    def active_value(d: pd.Timestamp) -> float:
        total = 0.0
        for p in sleeve_position.values():
            if p is None:
                continue
            px = _close(histories[p.isin], d)
            if px is not None:
                total += p.units * px
        return total

    def fallback_value(d: pd.Timestamp) -> float:
        px = world_px(d)
        return sum(sleeve_world_units.values()) * px

    def equity(d: pd.Timestamp) -> tuple[float, float, float]:
        av = active_value(d)
        fv = fallback_value(d)
        return av + fv, av, fv

    def reserved_sleeves() -> set[int]:
        return {int(x["sleeve_id"]) for x in pending_entries}

    def schedule_exit(isin: str, signal_date: pd.Timestamp, reason: str, score: float | None) -> pd.Timestamp | None:
        if isin in pending_exits or isin not in position_sleeve:
            return None
        nxt = _next_obs(histories[isin], signal_date)
        if nxt is None:
            return None
        pending_exits[isin] = {"exec_date": nxt[0], "reason": reason, "score": score, "sleeve_id": position_sleeve[isin]}
        return nxt[0]

    def schedule_entry(isin: str, signal_date: pd.Timestamp, score: float, peer: str,
                       earliest: pd.Timestamp | None = None, sleeve_id: int | None = None):
        if isin == WORLD_ISIN or isin in position_sleeve or any(x["isin"] == isin for x in pending_entries):
            return
        if sleeve_id is None:
            reserved = reserved_sleeves()
            free = [sid for sid in range(max_pos) if sleeve_position[sid] is None and sid not in reserved]
            if not free:
                return
            sleeve_id = free[0]
        elif any(int(x["sleeve_id"]) == sleeve_id for x in pending_entries):
            return
        anchor = signal_date if earliest is None else max(signal_date, earliest - pd.Timedelta(nanoseconds=1))
        nxt = _next_obs(histories[isin], anchor)
        if nxt is not None:
            pending_entries.append({"isin": isin, "exec_date": nxt[0], "score": score, "peer": peer, "sleeve_id": sleeve_id})

    def execute_exit(isin: str, d: pd.Timestamp, meta: dict):
        nonlocal turnover
        sid = position_sleeve.get(isin)
        if sid is None:
            return
        p = sleeve_position[sid]
        if p is None:
            return
        px = _close(histories[isin], d, exact=True)
        if px is None:
            return
        gross = p.units * px
        after_exit = gross * (1.0 - exit_fee)
        wpx = world_px(d)
        fallback_gross = after_exit / (1.0 + fallback_fee)
        sleeve_world_units[sid] = fallback_gross / wpx
        turnover += gross + fallback_gross
        net_ret = (1.0 - entry_fee) * (px / p.entry_price) * (1.0 - exit_fee) - 1.0
        trades.append(ClosedTrade(variant, isin, str(p.entry_date.date()), str(d.date()), p.entry_price, px,
                                  p.entry_score, meta.get("score"), float(net_ret), p.holding_sessions, meta["reason"]))
        sleeve_position[sid] = None
        position_sleeve.pop(isin, None)

    def execute_entry(order: dict, d: pd.Timestamp):
        nonlocal turnover
        isin = order["isin"]
        sid = int(order["sleeve_id"])
        if isin in position_sleeve or sleeve_position[sid] is not None:
            return
        px = _close(histories[isin], d, exact=True)
        if px is None:
            return
        wpx = world_px(d)
        world_gross = sleeve_world_units[sid] * wpx
        if world_gross <= 0:
            return
        after_world_sale = world_gross * (1.0 - fallback_fee)
        active_gross = after_world_sale / (1.0 + entry_fee)
        if active_gross <= 0:
            return
        sleeve_world_units[sid] = 0.0
        units = active_gross / px
        turnover += world_gross + active_gross
        p = Position(isin, units, d, px, float(order["score"]), str(order["peer"]), px, 0)
        sleeve_position[sid] = p
        position_sleeve[isin] = sid

    for d in dates:
        for isin, meta in list(pending_exits.items()):
            if meta["exec_date"] == d:
                execute_exit(isin, d, meta)
                pending_exits.pop(isin, None)
        todays = [x for x in pending_entries if x["exec_date"] == d]
        pending_entries = [x for x in pending_entries if x["exec_date"] != d]
        for order in todays:
            execute_entry(order, d)

        for isin, p in list(positions().items()):
            if d not in histories[isin].index or isin in pending_exits:
                continue
            m = _history_metrics(histories[isin], d)
            if m["close"] is None:
                continue
            p.holding_sessions += 1
            p.peak_price = max(p.peak_price, float(m["close"]))
            if float(m["close"] / p.entry_price - 1.0) <= stop:
                schedule_exit(isin, d, "CATASTROPHIC_STOP", latest_scores.get(isin, {}).get("score"))

        if d in review_dates:
            universe = _research_universe_as_of(histories, allowed, d)
            if len(universe) >= 3:
                snap, _ = score_grok2(universe, ref, base_cfg, g2_cfg)
                latest_scores = _score_map(snap)
                ranked = sorted([(i, v) for i, v in latest_scores.items() if v["decision"] in {"ELIGIBLE", "BUY_CANDIDATE"}],
                                key=lambda x: x[1]["score"], reverse=True)

                for isin, p in list(positions().items()):
                    if isin in pending_exits or p.holding_sessions < min_hold:
                        continue
                    if _reversal(_history_metrics(histories[isin], d), min_rev):
                        schedule_exit(isin, d, "STRICT_MONTHLY_REVERSAL", latest_scores.get(isin, {}).get("score"))
                        continue
                    if variant == "G_WORLD_FALLBACK_RELATIVE_EXIT" and p.holding_sessions >= rel_min_hold:
                        rel = _relative_perf63(histories, isin, d)
                        cur_score = latest_scores.get(isin, {}).get("score")
                        if rel is not None and rel <= rel_floor and (cur_score is None or cur_score <= rel_score):
                            schedule_exit(isin, d, "RELATIVE_WEAKNESS_TO_WORLD", cur_score)

                if variant in {"F_WORLD_FALLBACK_ROTATION", "G_WORLD_FALLBACK_RELATIVE_EXIT"}:
                    for cand, cv in ranked:
                        if cand in position_sleeve or any(x["isin"] == cand for x in pending_entries):
                            continue
                        cm = _history_metrics(histories[cand], d)
                        if cm["perf63"] is None:
                            continue
                        replacement = None
                        for held, hp in positions().items():
                            if held in pending_exits or hp.holding_sessions < min_hold:
                                continue
                            hv = latest_scores.get(held)
                            hm = _history_metrics(histories[held], d)
                            if hv is None or hm["perf63"] is None:
                                continue
                            adv = cv["score"] - hv["score"]
                            padv = cm["perf63"] - hm["perf63"]
                            if adv >= rot_score and padv >= rot_perf:
                                key = adv + 100.0 * padv
                                if replacement is None or key > replacement[0]:
                                    replacement = (key, held)
                        if replacement is not None:
                            held = replacement[1]
                            sid = position_sleeve[held]
                            exd = schedule_exit(held, d, "ROTATION_TO_STRONGER_ETF", latest_scores.get(held, {}).get("score"))
                            if exd is not None:
                                schedule_entry(cand, d, cv["score"], cv["peer_group"], earliest=exd, sleeve_id=sid)
                            break

                projected = set(position_sleeve) - set(pending_exits)
                projected.update(x["isin"] for x in pending_entries)
                for cand, cv in ranked:
                    if len(projected) >= max_pos:
                        break
                    if cand in projected or cand == WORLD_ISIN:
                        continue
                    schedule_entry(cand, d, cv["score"], cv["peer_group"])
                    if any(x["isin"] == cand for x in pending_entries):
                        projected.add(cand)

        eq, av, fv = equity(d)
        world_sleeves = sum(1 for sid in range(max_pos) if sleeve_position[sid] is None)
        equity_rows.append({"date": d, "equity": eq, "active_grok": av, "world_fallback": fv, "cash": 0.0,
                            "active_sleeves": max_pos - world_sleeves, "world_sleeves": world_sleeves})
        active_utilisation.append(0.0 if eq <= 0 else av / eq)

    for isin, p in positions().items():
        px = _close(histories[isin], end_ts)
        if px is None:
            continue
        net_ret = (1.0 - entry_fee) * (px / p.entry_price) - 1.0
        trades.append(ClosedTrade(variant, isin, str(p.entry_date.date()), str(end_ts.date()), p.entry_price, px,
                                  p.entry_score, latest_scores.get(isin, {}).get("score"), float(net_ret), p.holding_sessions, "END_OF_DATA"))
    edf = pd.DataFrame(equity_rows)
    tdf = pd.DataFrame([asdict(x) for x in trades])
    stats = _stats(edf.rename(columns={"active_grok": "invested"}), tdf, initial, active_utilisation, turnover)
    stats["active_grok_utilisation"] = stats.pop("capital_utilisation")
    stats["world_fallback_utilisation"] = float(1.0 - stats["active_grok_utilisation"])
    stats["allocation_model"] = "TWO_INDEPENDENT_50PCT_SLEEVES"
    stats["cash_target"] = 0.0
    return stats, tdf, edf


def run(root: Path = ROOT, start: str = "2013-01-01", end: str | None = None) -> dict:
    base_cfg = _json(root / "config/V20.8_ETF_GROK_HIGH_PRECISION.json")
    g2_cfg = _json(root / "config/ETF_GROK2_CDC_V1.json")
    cfg = _json(root / "config/ETF_GROK2_EXIT_RESEARCH_V3.json")
    v2_cfg = _json(root / "config/ETF_GROK2_EXIT_RESEARCH_V2.json")
    histories = _load_histories(root)
    allowed = set(_quality_eligible(root))
    ref = load_master(root / "inputs/V18.2_PEA_ETF_MASTER.csv")
    end = end or str(max(f.index.max() for f in histories.values() if not f.empty).date())
    initial = float(cfg["portfolio"]["initial_capital_eur"])
    fee = float(cfg["portfolio"]["active_entry_fee_bps"]) / 10000.0
    world = _world_benchmark(histories, pd.Timestamp(start), pd.Timestamp(end), initial, fee)
    outdir = root / "outputs/etf_grok2_world_fallback_research_v3"
    outdir.mkdir(parents=True, exist_ok=True)

    variants = {}
    control, ct, ce = simulate_v2("A_REVERSAL_STRICT", histories, allowed, ref, base_cfg, g2_cfg, v2_cfg, start, end)
    variants["A_STRICT_CASH"] = control
    ct.to_csv(outdir / "A_STRICT_CASH_TRADES.csv", index=False)
    ce.to_csv(outdir / "A_STRICT_CASH_EQUITY.csv", index=False)
    for variant in ["E_WORLD_FALLBACK", "F_WORLD_FALLBACK_ROTATION", "G_WORLD_FALLBACK_RELATIVE_EXIT"]:
        stats, trades, eq = simulate_world_variant(variant, histories, allowed, ref, base_cfg, g2_cfg, cfg, start, end)
        variants[variant] = stats
        trades.to_csv(outdir / f"{variant}_TRADES.csv", index=False)
        eq.to_csv(outdir / f"{variant}_EQUITY.csv", index=False)

    for stats in variants.values():
        stats["cagr_delta_vs_world"] = float(stats["cagr"] - world["cagr"])
        stats["final_equity_delta_vs_world"] = float(stats["final_equity"] - world["final_equity"])
    ranked = sorted(variants, key=lambda k: (variants[k]["cagr_delta_vs_world"], variants[k].get("calmar") or -999), reverse=True)
    result = {"version": cfg["version"], "status": cfg["status"], "start": start, "end": end,
              "world_benchmark": world, "variants": variants, "ranking_by_cagr_vs_world": ranked,
              "best_variant": ranked[0], "fixed_take_profit_used": False, "same_close_signal_execution_used": False,
              "score_exhaustion_exit_used": False, "allocation_model": "TWO_INDEPENDENT_50PCT_SLEEVES",
              "survivorship_bias_resolved": False, "promotion_eligible": False, "real_orders_allowed": False}
    (outdir / "SUMMARY.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    run()
