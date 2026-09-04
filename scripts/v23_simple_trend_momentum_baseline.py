from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

INITIAL_CAPITAL = 65_000.0
FEE_EACH_SIDE = 0.002
STRESS_SLIPPAGE_EACH_SIDE = 0.001
WARMUP_START = "2008-01-01"
EVAL_START = pd.Timestamp("2010-01-01")
EVAL_END = pd.Timestamp("2023-01-01")
SMA_DAYS = 200
MOM_DAYS = 252


def load_prices(symbol: str) -> pd.DataFrame:
    df = yf.download(symbol, start=WARMUP_START, end=str(EVAL_END.date()), auto_adjust=False, progress=False, actions=True)
    if df.empty:
        raise SystemExit(f"BLOCK_V23_SIMPLE_DATA: no data for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]) for c in df.columns]
    df = df.reset_index()
    dcol = next((c for c in df.columns if str(c).lower() in {"date", "datetime"}), None)
    if dcol is None:
        raise SystemExit("BLOCK_V23_SIMPLE_DATA: date missing")
    pxcol = "Adj Close" if "Adj Close" in df.columns else "Close"
    df["date"] = pd.to_datetime(df[dcol], errors="coerce").dt.tz_localize(None).dt.normalize()
    df["px"] = pd.to_numeric(df[pxcol], errors="coerce")
    df = df.dropna(subset=["date", "px"])
    df = df[df["px"] > 0].drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    if len(df) < MOM_DAYS + 252:
        raise SystemExit("BLOCK_V23_SIMPLE_DATA: insufficient warmup")
    return df[["date", "px"]].copy()


def max_drawdown(x: pd.Series) -> float:
    return float((x / x.cummax() - 1.0).min())


def cagr(first: float, last: float, days: int) -> float:
    years = max(days / 365.2425, 1e-9)
    return float((last / first) ** (1.0 / years) - 1.0)


def build_schedule(prices: pd.DataFrame) -> pd.DataFrame:
    z = prices.copy()
    z["sma200"] = z["px"].rolling(SMA_DAYS, min_periods=SMA_DAYS).mean()
    z["mom12m"] = z["px"] / z["px"].shift(MOM_DAYS) - 1.0
    z["month"] = z["date"].dt.to_period("M")
    month_end_idx = z.groupby("month", sort=True).tail(1).index
    z["signal"] = np.nan
    z.loc[month_end_idx, "signal"] = ((z.loc[month_end_idx, "px"] > z.loc[month_end_idx, "sma200"]) & (z.loc[month_end_idx, "mom12m"] > 0)).astype(int)
    z["target_next"] = z["signal"].ffill().shift(1)
    # The shift ensures a signal observed at a close is never executed on that same close.
    z = z[(z["date"] >= EVAL_START) & (z["date"] < EVAL_END)].copy()
    z["target_next"] = z["target_next"].fillna(0).astype(int)
    return z.reset_index(drop=True)


def simulate(z: pd.DataFrame, stress: bool) -> tuple[pd.DataFrame, dict]:
    extra = STRESS_SLIPPAGE_EACH_SIDE if stress else 0.0
    cash = INITIAL_CAPITAL
    shares = 0
    equity_rows = []
    trades = []
    prev_target = 0
    invested_days = 0

    for i, row in z.iterrows():
        px = float(row["px"])
        target = int(row["target_next"])
        date = pd.Timestamp(row["date"])
        if target != prev_target:
            if target == 1 and shares == 0:
                ep = px * (1.0 + extra)
                qty = int(np.floor(cash / (ep * (1.0 + FEE_EACH_SIDE))))
                if qty > 0:
                    fee = qty * ep * FEE_EACH_SIDE
                    cash -= qty * ep + fee
                    shares += qty
                    trades.append({"date": date, "side": "BUY", "px": ep, "shares": qty, "fee": fee})
            elif target == 0 and shares > 0:
                ep = px * (1.0 - extra)
                fee = shares * ep * FEE_EACH_SIDE
                cash += shares * ep - fee
                trades.append({"date": date, "side": "SELL", "px": ep, "shares": shares, "fee": fee})
                shares = 0
            prev_target = target
        if shares > 0:
            invested_days += 1
        equity_rows.append({"date": date, "px": px, "target": target, "cash": cash, "shares": shares, "equity_eur": cash + shares * px})

    if shares > 0:
        px = float(z.iloc[-1]["px"]) * (1.0 - extra)
        date = pd.Timestamp(z.iloc[-1]["date"])
        fee = shares * px * FEE_EACH_SIDE
        cash += shares * px - fee
        trades.append({"date": date, "side": "SELL_FINAL", "px": px, "shares": shares, "fee": fee})
        shares = 0

    eq = pd.DataFrame(equity_rows)
    final = float(cash)
    days = int((pd.Timestamp(z.iloc[-1]["date"]) - pd.Timestamp(z.iloc[0]["date"])).days)
    daily = eq.set_index("date")["equity_eur"].pct_change().dropna()
    buys = sum(1 for t in trades if t["side"] == "BUY")
    metrics = {
        "mode": "stress" if stress else "base",
        "initial_capital_eur": INITIAL_CAPITAL,
        "final_liquidation_eur": final,
        "net_eur": float(final - INITIAL_CAPITAL),
        "net_return": float(final / INITIAL_CAPITAL - 1.0),
        "cagr": cagr(INITIAL_CAPITAL, final, days),
        "max_drawdown": max_drawdown(eq["equity_eur"]),
        "annualized_volatility": float(daily.std(ddof=1) * np.sqrt(252)) if len(daily) > 1 else None,
        "exposure_fraction_days": float(invested_days / len(eq)),
        "round_trip_entries": int(buys),
        "transaction_count": int(len(trades)),
        "total_fees_eur": float(sum(t["fee"] for t in trades)),
        "fee_each_side": FEE_EACH_SIDE,
        "stress_slippage_each_side": extra,
    }
    return eq, metrics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="CW8.PA")
    ap.add_argument("--out-dir", type=Path, default=Path("outputs/v23_simple_baseline"))
    a = ap.parse_args()

    prices = load_prices(a.symbol)
    sched = build_schedule(prices)
    if sched.empty or sched["date"].max() >= EVAL_END:
        raise SystemExit("BLOCK_V23_SIMPLE_GOVERNANCE")
    base_eq, base = simulate(sched, False)
    stress_eq, stress = simulate(sched, True)

    out = a.out_dir
    out.mkdir(parents=True, exist_ok=True)
    sched.to_csv(out / "SIMPLE_SIGNAL_SCHEDULE_PRE2023.csv", index=False)
    base_eq.to_csv(out / "SIMPLE_EQUITY_BASE_PRE2023.csv", index=False)
    stress_eq.to_csv(out / "SIMPLE_EQUITY_STRESS_PRE2023.csv", index=False)
    report = {
        "version": "TABPORT_V23_SIMPLE_BASELINE_1",
        "symbol": a.symbol,
        "rule": "monthly close signal: adjusted_close > SMA200 AND 12m momentum > 0; execute no earlier than next trading session; otherwise cash",
        "parameters": {"sma_days": SMA_DAYS, "momentum_days": MOM_DAYS, "rebalance": "monthly", "cash_return": 0.0},
        "governance": {
            "holdout_2023_2026_accessed": False,
            "evaluation_start": str(EVAL_START.date()),
            "evaluation_end_exclusive": str(EVAL_END.date()),
            "variant_count": 1,
            "parameters_predeclared_not_tuned": True,
            "same_day_execution_forbidden": True
        },
        "base": base,
        "stress": stress,
        "stress_cagr_delta": float(stress["cagr"] - base["cagr"]),
    }
    (out / "SIMPLE_BASELINE_REPORT_PRE2023.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
