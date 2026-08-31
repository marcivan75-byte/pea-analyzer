from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.tseries.offsets import BDay

INITIAL_CAPITAL = 65000.0
MAX_LINE = 4500.0
MAX_LINES = 12
FEE_SIDE = 0.002
STRESS_SLIPPAGE_SIDE = 0.001


def metrics(trades: pd.DataFrame, equity: pd.DataFrame, initial: float) -> dict:
    if trades.empty:
        return {"trades": 0, "net_eur": 0.0, "net_pct": 0.0, "cagr": None, "profit_factor": None, "win_rate": None, "max_drawdown": None, "fees_eur": 0.0, "avg_capital_utilization": None}
    pnl = trades["net_pnl_eur"].astype(float)
    gp = float(pnl[pnl > 0].sum())
    gl = float((-pnl[pnl <= 0]).sum())
    end = float(equity["equity_eur"].iloc[-1])
    start_date = pd.Timestamp(equity["date"].iloc[0])
    end_date = pd.Timestamp(equity["date"].iloc[-1])
    years = max((end_date - start_date).days / 365.25, 1 / 365.25)
    eq = equity["equity_eur"].astype(float)
    dd = eq / eq.cummax() - 1.0
    return {
        "trades": int(len(trades)),
        "net_eur": float(end - initial),
        "net_pct": float(end / initial - 1.0),
        "cagr": float((end / initial) ** (1.0 / years) - 1.0) if end > 0 else -1.0,
        "profit_factor": float(gp / gl) if gl > 0 else None,
        "win_rate": float((pnl > 0).mean()),
        "max_drawdown": float(dd.min()),
        "fees_eur": float(trades["fees_eur"].sum()),
        "avg_capital_utilization": float(equity["capital_utilization"].mean()),
        "max_open_lines": int(equity["open_lines"].max()),
    }


def simulate(candidates: pd.DataFrame, slippage_side: float) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    cash = INITIAL_CAPITAL
    positions: list[dict] = []
    trades: list[dict] = []
    events: list[dict] = []
    rejected_slots = rejected_cash = rejected_price = 0

    cands = candidates.sort_values(["entry_date", "rank_score", "ticker"], ascending=[True, False, True], kind="stable")

    def close_due(now: pd.Timestamp) -> None:
        nonlocal cash, positions
        still = []
        for p in positions:
            if p["exit_date"] <= now:
                gross_exit_price = p["entry_price_raw"] * (1.0 + p["ret26"])
                exit_price = gross_exit_price * (1.0 - slippage_side)
                proceeds = p["shares"] * exit_price
                exit_fee = proceeds * FEE_SIDE
                cash += proceeds - exit_fee
                net_pnl = proceeds - exit_fee - p["cash_out"]
                trades.append({**p, "exit_price_exec": exit_price, "exit_fee_eur": exit_fee, "fees_eur": p["entry_fee_eur"] + exit_fee, "net_pnl_eur": net_pnl})
            else:
                still.append(p)
        positions = still

    for _, r in cands.iterrows():
        entry_date = pd.Timestamp(r["entry_date"])
        close_due(entry_date)
        invested = sum(p["cash_out"] for p in positions)
        events.append({"date": entry_date, "equity_eur": cash + invested, "cash_eur": cash, "invested_cost_eur": invested, "open_lines": len(positions), "capital_utilization": invested / INITIAL_CAPITAL})
        raw_price = float(r["entry_price"])
        if not np.isfinite(raw_price) or raw_price <= 0:
            rejected_price += 1
            continue
        if len(positions) >= MAX_LINES:
            rejected_slots += 1
            continue
        exec_price = raw_price * (1.0 + slippage_side)
        # Fee is part of line cash usage; integer shares only.
        per_share_cash = exec_price * (1.0 + FEE_SIDE)
        budget = min(MAX_LINE, cash)
        shares = int(np.floor(budget / per_share_cash))
        if shares < 1:
            rejected_cash += 1
            continue
        notional = shares * exec_price
        entry_fee = notional * FEE_SIDE
        cash_out = notional + entry_fee
        if cash_out > cash + 1e-9:
            rejected_cash += 1
            continue
        cash -= cash_out
        hit_stop = bool(r["hit_stop"])
        if hit_stop and pd.notna(r["day_stop"]):
            exit_date = entry_date + BDay(max(int(r["day_stop"]), 0))
        else:
            exit_date = pd.Timestamp(r["label_end_date_26w"])
        positions.append({
            "signal_date": pd.Timestamp(r["date"]), "entry_date": entry_date, "exit_date": exit_date,
            "ticker": str(r["ticker"]), "isin": str(r["isin"]), "rank_score": float(r["rank_score"]),
            "shares": shares, "entry_price_raw": raw_price, "entry_price_exec": exec_price,
            "entry_fee_eur": entry_fee, "cash_out": cash_out, "ret26": float(r["forward_ret_true_26w"]),
            "hit_stop": hit_stop, "day_stop": None if pd.isna(r["day_stop"]) else int(r["day_stop"]),
        })

    # Close all remaining positions at their governed exit dates in order.
    for d in sorted({p["exit_date"] for p in positions}):
        close_due(pd.Timestamp(d))
        invested = sum(p["cash_out"] for p in positions)
        events.append({"date": pd.Timestamp(d), "equity_eur": cash + invested, "cash_eur": cash, "invested_cost_eur": invested, "open_lines": len(positions), "capital_utilization": invested / INITIAL_CAPITAL})

    td = pd.DataFrame(trades)
    ed = pd.DataFrame(events).sort_values("date", kind="stable").drop_duplicates("date", keep="last")
    diag = {"rejected_slots": rejected_slots, "rejected_cash": rejected_cash, "rejected_price": rejected_price, "candidate_count": int(len(cands))}
    return td, ed, diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--pass4-selected", type=Path, required=True)
    ap.add_argument("--pass4-report", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()

    p4 = json.loads(a.pass4_report.read_text(encoding="utf-8"))
    gov = p4.get("governance", {})
    if gov.get("holdout_accessed") is not False or p4.get("selected", {}).get("variant") != "RISK_ADD_ADAPT":
        raise SystemExit("BLOCK_PASS5_GOVERNANCE: pass4 not frozen")
    if int(gov.get("embargo_weeks", 0)) != 26:
        raise SystemExit("BLOCK_PASS5_GOVERNANCE: embargo not proven")

    selected = pd.read_csv(a.pass4_selected, low_memory=False)
    train = pd.read_csv(a.train, usecols=["as_of_date", "ticker", "isin", "entry_date", "entry_price", "forward_ret_true_26w", "label_end_date_26w", "hit_stop", "day_stop"], low_memory=False)
    train["date"] = pd.to_datetime(train["as_of_date"], errors="coerce")
    selected["date"] = pd.to_datetime(selected["date"], errors="coerce")
    keys = ["date", "ticker", "isin"]
    m = selected.merge(train.drop(columns=["as_of_date"]), on=keys, how="left", validate="one_to_one")
    if m[["entry_date", "entry_price", "forward_ret_true_26w", "label_end_date_26w"]].isna().any().any():
        raise SystemExit("BLOCK_PASS5_DATA: governed execution fields missing")
    m["entry_date"] = pd.to_datetime(m["entry_date"], errors="coerce")
    m["label_end_date_26w"] = pd.to_datetime(m["label_end_date_26w"], errors="coerce")
    m["rank_score"] = pd.to_numeric(m["RISK_ADD_ADAPT"], errors="coerce")
    if not np.isfinite(m["rank_score"]).all():
        raise SystemExit("BLOCK_PASS5_DATA: rank score invalid")

    base_t, base_e, base_d = simulate(m, 0.0)
    stress_t, stress_e, stress_d = simulate(m, STRESS_SLIPPAGE_SIDE)
    if base_t.empty or stress_t.empty:
        raise SystemExit("BLOCK_PASS5_PORTFOLIO: no executable trades")
    bm = metrics(base_t, base_e, INITIAL_CAPITAL)
    sm = metrics(stress_t, stress_e, INITIAL_CAPITAL)
    # Robustness is descriptive: do not tune any upstream rule here. Fail closed only on implausible accounting.
    if bm["max_open_lines"] > MAX_LINES or sm["max_open_lines"] > MAX_LINES:
        raise SystemExit("BLOCK_PASS5_PORTFOLIO: max-lines breach")
    if float(base_t["cash_out"].max()) > MAX_LINE + 1e-6 or float(stress_t["cash_out"].max()) > MAX_LINE + 1e-6:
        raise SystemExit("BLOCK_PASS5_PORTFOLIO: line-cap breach")

    robustness = {
        "cagr_delta_stress": None if bm["cagr"] is None or sm["cagr"] is None else float(sm["cagr"] - bm["cagr"]),
        "net_eur_delta_stress": float(sm["net_eur"] - bm["net_eur"]),
        "pf_ratio_stress_to_base": float(sm["profit_factor"] / bm["profit_factor"]) if bm["profit_factor"] and sm["profit_factor"] else None,
        "slippage_dependency_flag": bool(bm["net_eur"] > 0 and sm["net_eur"] <= 0),
    }
    report = {
        "version": "V22.1_TABPORT_PASS5_REALISTIC_PORTFOLIO_1",
        "governance": {"holdout_accessed": False, "holdout_scope": "SEALED_UNTIL_FINAL_PASS6_EVALUATION", "upstream_ranking_frozen": True, "embargo_weeks": 26, "survivorship_bias_disclosure_required": True},
        "execution": {"initial_capital_eur": INITIAL_CAPITAL, "max_line_eur": MAX_LINE, "max_lines": MAX_LINES, "integer_shares": True, "fee_each_side": FEE_SIDE, "stress_slippage_each_side": STRESS_SLIPPAGE_SIDE, "entry_price_source": "governed entry_price", "exit_return_source": "governed forward_ret_true_26w incl stop cap", "stopped_exit_date_proxy": "entry_date + day_stop business days", "nonstop_exit_date_source": "label_end_date_26w", "drawdown_basis": "event-level realized/cost-basis equity proxy; not daily mark-to-market"},
        "base": bm, "stress": sm, "robustness": robustness, "diagnostics": {"base": base_d, "stress": stress_d},
        "promotion_automatic": False,
    }
    a.out_dir.mkdir(parents=True, exist_ok=True)
    base_t.to_csv(a.out_dir / "PASS5_TRADES_BASE.csv", index=False); stress_t.to_csv(a.out_dir / "PASS5_TRADES_STRESS.csv", index=False)
    base_e.to_csv(a.out_dir / "PASS5_EQUITY_BASE.csv", index=False); stress_e.to_csv(a.out_dir / "PASS5_EQUITY_STRESS.csv", index=False)
    (a.out_dir / "PASS5_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
