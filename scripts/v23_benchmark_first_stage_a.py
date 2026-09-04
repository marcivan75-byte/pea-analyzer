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
DEFAULT_START = "2010-01-01"
DEFAULT_END = "2023-01-01"  # Stage A selection/evaluation is pre-2023 only.


def load_prices(symbol: str, start: str, end: str) -> pd.DataFrame:
    df = yf.download(symbol, start=start, end=end, auto_adjust=False, progress=False, actions=True)
    if df.empty:
        raise SystemExit(f"BLOCK_V23_BENCHMARK_DATA: no data for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]) for c in df.columns]
    df = df.reset_index()
    date_col = next((c for c in df.columns if str(c).lower() in {"date", "datetime"}), None)
    if date_col is None:
        raise SystemExit("BLOCK_V23_BENCHMARK_DATA: date column missing")
    df["date"] = pd.to_datetime(df[date_col], errors="coerce").dt.tz_localize(None).dt.normalize()
    px_col = "Adj Close" if "Adj Close" in df.columns else "Close"
    df["px"] = pd.to_numeric(df[px_col], errors="coerce")
    df = df.dropna(subset=["date", "px"])
    df = df[df["px"] > 0].drop_duplicates("date", keep="last").sort_values("date")
    if len(df) < 252:
        raise SystemExit(f"BLOCK_V23_BENCHMARK_DATA: insufficient history rows={len(df)}")
    return df[["date", "px"]].copy()


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def cagr(first: float, last: float, days: int) -> float:
    years = max(days / 365.2425, 1e-9)
    return float((last / first) ** (1.0 / years) - 1.0)


def buy_hold(prices: pd.DataFrame, stress: bool = False) -> tuple[pd.DataFrame, dict]:
    z = prices.copy()
    entry_px = float(z.iloc[0]["px"])
    extra = STRESS_SLIPPAGE_EACH_SIDE if stress else 0.0
    effective_entry = entry_px * (1.0 + extra)
    per_share_cost = effective_entry * (1.0 + FEE_EACH_SIDE)
    shares = int(np.floor(INITIAL_CAPITAL / per_share_cost))
    if shares < 1:
        raise SystemExit("BLOCK_V23_BENCHMARK_EXECUTION: zero shares")
    entry_fee = shares * effective_entry * FEE_EACH_SIDE
    cash = INITIAL_CAPITAL - shares * effective_entry - entry_fee
    z["equity_eur"] = cash + shares * z["px"]
    final_mark = float(z.iloc[-1]["px"])
    effective_exit = final_mark * (1.0 - extra)
    exit_fee = shares * effective_exit * FEE_EACH_SIDE
    final_liquidation = cash + shares * effective_exit - exit_fee
    total_return = final_liquidation / INITIAL_CAPITAL - 1.0
    days = int((z.iloc[-1]["date"] - z.iloc[0]["date"]).days)
    daily = z.set_index("date")["equity_eur"].pct_change().dropna()
    metrics = {
        "mode": "stress" if stress else "base",
        "start": str(z.iloc[0]["date"].date()),
        "end": str(z.iloc[-1]["date"].date()),
        "initial_capital_eur": INITIAL_CAPITAL,
        "shares": shares,
        "entry_px": entry_px,
        "entry_fee_eur": float(entry_fee),
        "exit_fee_eur": float(exit_fee),
        "final_liquidation_eur": float(final_liquidation),
        "net_eur": float(final_liquidation - INITIAL_CAPITAL),
        "net_return": float(total_return),
        "cagr": cagr(INITIAL_CAPITAL, final_liquidation, days),
        "max_drawdown": max_drawdown(z["equity_eur"]),
        "annualized_volatility": float(daily.std(ddof=1) * np.sqrt(252)) if len(daily) > 1 else None,
        "capital_utilization_at_entry": float((INITIAL_CAPITAL - cash) / INITIAL_CAPITAL),
        "fee_each_side": FEE_EACH_SIDE,
        "stress_slippage_each_side": extra,
        "price_field": "adjusted_or_close_from_yfinance"
    }
    return z, metrics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="CW8.PA")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=DEFAULT_END)
    ap.add_argument("--out-dir", type=Path, default=Path("outputs/v23_benchmark_stage_a"))
    args = ap.parse_args()

    # Hard guard: this Stage A script must not consume the observed V22.1 holdout.
    if pd.Timestamp(args.end) > pd.Timestamp("2023-01-01"):
        raise SystemExit("BLOCK_V23_GOVERNANCE: Stage A end must be <= 2023-01-01")

    prices = load_prices(args.symbol, args.start, args.end)
    base_curve, base = buy_hold(prices, stress=False)
    stress_curve, stress = buy_hold(prices, stress=True)

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    prices.to_csv(out / "BENCHMARK_PRICES_PRE2023.csv", index=False)
    base_curve.to_csv(out / "BENCHMARK_EQUITY_BASE_PRE2023.csv", index=False)
    stress_curve.to_csv(out / "BENCHMARK_EQUITY_STRESS_PRE2023.csv", index=False)
    report = {
        "version": "TABPORT_V23_STAGE_A_BENCHMARK_1",
        "symbol": args.symbol,
        "governance": {
            "holdout_2023_2026_accessed": False,
            "selection_period_end_exclusive": args.end,
            "benchmark_fixed_before_any_v23_stock_pick_test": True,
            "variant_count": 1,
            "purpose": "passive benchmark baseline only"
        },
        "base": base,
        "stress": stress,
        "stress_cagr_delta": None if base["cagr"] is None or stress["cagr"] is None else float(stress["cagr"] - base["cagr"]),
    }
    (out / "BENCHMARK_REPORT_PRE2023.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
