from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

INITIAL_CAPITAL = 65_000.0
FEE_EACH_SIDE = 0.002
STRESS_SLIPPAGE_EACH_SIDE = 0.001
CUTOFF = pd.Timestamp("2023-01-01")
TOP_N = 10
LOOKBACK_DAYS = 252
SKIP_DAYS = 21
MIN_HISTORY = 252


def _norm_cols(df: pd.DataFrame) -> pd.DataFrame:
    m = {str(c).strip().lower(): c for c in df.columns}
    def pick(*names: str) -> str:
        for n in names:
            if n in m:
                return m[n]
        raise SystemExit(f"BLOCK_V23_SCHEMA: missing one of {names}; got={list(df.columns)[:50]}")
    out = pd.DataFrame({
        "isin": df[pick("isin")].astype(str),
        "date": pd.to_datetime(df[pick("date", "datetime")], errors="coerce").dt.tz_localize(None).dt.normalize(),
        "close": pd.to_numeric(df[pick("close", "adj_close", "adjusted_close")], errors="coerce"),
    })
    if "volume" in m:
        out["volume"] = pd.to_numeric(df[m["volume"]], errors="coerce")
    return out.dropna(subset=["isin", "date", "close"])


def load_history(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    z = _norm_cols(df)
    # Governance: post-2022 rows are discarded immediately and cannot enter feature construction.
    z = z[z["date"] < CUTOFF].copy()
    z = z[z["close"] > 0]
    z = z.drop_duplicates(["isin", "date"], keep="last").sort_values(["isin", "date"])
    if z.empty or z["date"].max() >= CUTOFF:
        raise SystemExit("BLOCK_V23_GOVERNANCE: invalid pre-2023 history")
    return z


def build_monthly_signals(z: pd.DataFrame) -> pd.DataFrame:
    # Predeclared single hypothesis: classic 12-1 cross-sectional momentum.
    # At each month-end t, rank stocks by close[t-21] / close[t-252] - 1.
    # No liquidity/volatility/risk filters are optimized here. Availability and positive price only.
    parts = []
    for isin, g in z.groupby("isin", sort=False):
        g = g.sort_values("date").copy()
        g["p_skip"] = g["close"].shift(SKIP_DAYS)
        g["p_12m"] = g["close"].shift(LOOKBACK_DAYS)
        g["mom_12_1"] = g["p_skip"] / g["p_12m"] - 1.0
        g["obs"] = np.arange(len(g)) + 1
        parts.append(g[["isin", "date", "close", "mom_12_1", "obs"]])
    x = pd.concat(parts, ignore_index=True)
    x["month"] = x["date"].dt.to_period("M")
    month_end = x.groupby(["isin", "month"], as_index=False)["date"].max().rename(columns={"date":"signal_date"})
    s = month_end.merge(x, left_on=["isin", "signal_date"], right_on=["isin", "date"], how="left")
    s = s[(s["obs"] >= MIN_HISTORY) & s["mom_12_1"].notna()].copy()
    s["rank"] = s.groupby("signal_date")["mom_12_1"].rank(method="first", ascending=False)
    s = s[s["rank"] <= TOP_N].sort_values(["signal_date", "rank", "isin"])
    return s[["signal_date", "isin", "close", "mom_12_1", "rank"]]


def next_price_map(z: pd.DataFrame) -> dict[tuple[str, pd.Timestamp], tuple[pd.Timestamp, float]]:
    out = {}
    for isin, g in z.groupby("isin", sort=False):
        d = g["date"].to_numpy()
        p = g["close"].to_numpy(dtype=float)
        for i in range(len(g)-1):
            out[(isin, pd.Timestamp(d[i]))] = (pd.Timestamp(d[i+1]), float(p[i+1]))
    return out


def simulate(z: pd.DataFrame, signals: pd.DataFrame, stress: bool=False) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    extra = STRESS_SLIPPAGE_EACH_SIDE if stress else 0.0
    dates = pd.DatetimeIndex(sorted(z["date"].unique()))
    close_by_date = {pd.Timestamp(d): g.set_index("isin")["close"].to_dict() for d, g in z.groupby("date")}
    signal_by_date = {pd.Timestamp(d): g.sort_values("rank")["isin"].tolist() for d, g in signals.groupby("signal_date")}
    next_map = next_price_map(z)

    cash = INITIAL_CAPITAL
    holdings: dict[str, int] = {}
    trades = []
    equity_rows = []
    pending_target: list[str] | None = None
    pending_from: pd.Timestamp | None = None

    for d in dates:
        d = pd.Timestamp(d)
        pxs = close_by_date.get(d, {})

        # Execute previously scheduled month-end decision on the first later date where names are priced.
        if pending_target is not None and d > pending_from:
            # Liquidate existing positions at today's close if priced; missing price => fail closed by retaining until priced.
            for isin in list(holdings):
                if isin in pending_target:
                    continue
                if isin not in pxs:
                    continue
                qty = holdings.pop(isin)
                px = float(pxs[isin]) * (1.0 - extra)
                fee = qty * px * FEE_EACH_SIDE
                cash += qty * px - fee
                trades.append({"date": d, "isin": isin, "side": "SELL", "qty": qty, "px": px, "fee": fee})

            target_available = [i for i in pending_target if i in pxs]
            if target_available:
                equity_before = cash + sum(q * float(pxs.get(i, 0.0)) for i, q in holdings.items())
                per_name = equity_before / TOP_N
                for isin in target_available:
                    if isin in holdings:
                        continue
                    px = float(pxs[isin]) * (1.0 + extra)
                    qty = int(np.floor(per_name / (px * (1.0 + FEE_EACH_SIDE))))
                    if qty < 1:
                        continue
                    fee = qty * px * FEE_EACH_SIDE
                    cost = qty * px + fee
                    if cost <= cash:
                        cash -= cost
                        holdings[isin] = qty
                        trades.append({"date": d, "isin": isin, "side": "BUY", "qty": qty, "px": px, "fee": fee})
            pending_target = None
            pending_from = None

        mark = cash + sum(q * float(pxs.get(i, np.nan)) for i, q in holdings.items() if i in pxs)
        # For any holding missing on this calendar date, use last known mark from its own series.
        if holdings:
            for i, q in holdings.items():
                if i not in pxs:
                    hist = z[(z["isin"] == i) & (z["date"] <= d)]
                    if not hist.empty:
                        mark += q * float(hist.iloc[-1]["close"])
        equity_rows.append({"date": d, "equity_eur": mark, "cash_eur": cash, "open_positions": len(holdings)})

        if d in signal_by_date:
            pending_target = signal_by_date[d][:TOP_N]
            pending_from = d

    # Liquidate at last observed close.
    last_date = dates[-1]
    pxs = close_by_date[last_date]
    for isin in list(holdings):
        if isin not in pxs:
            hist = z[(z["isin"] == isin) & (z["date"] <= last_date)]
            if hist.empty:
                continue
            raw = float(hist.iloc[-1]["close"])
        else:
            raw = float(pxs[isin])
        qty = holdings.pop(isin)
        px = raw * (1.0 - extra)
        fee = qty * px * FEE_EACH_SIDE
        cash += qty * px - fee
        trades.append({"date": last_date, "isin": isin, "side": "SELL_FINAL", "qty": qty, "px": px, "fee": fee})

    eq = pd.DataFrame(equity_rows).drop_duplicates("date", keep="last").sort_values("date")
    if not eq.empty:
        eq.loc[eq.index[-1], "equity_eur"] = cash
        eq.loc[eq.index[-1], "cash_eur"] = cash
        eq.loc[eq.index[-1], "open_positions"] = 0
    tr = pd.DataFrame(trades)
    peak = eq["equity_eur"].cummax()
    dd = eq["equity_eur"] / peak - 1.0
    daily = eq.set_index("date")["equity_eur"].pct_change().dropna()
    days = int((eq.iloc[-1]["date"] - eq.iloc[0]["date"]).days)
    years = max(days / 365.2425, 1e-9)
    final = float(eq.iloc[-1]["equity_eur"])
    metrics = {
        "mode": "stress" if stress else "base",
        "start": str(eq.iloc[0]["date"].date()),
        "end": str(eq.iloc[-1]["date"].date()),
        "initial_capital_eur": INITIAL_CAPITAL,
        "final_equity_eur": final,
        "net_eur": final - INITIAL_CAPITAL,
        "net_return": final / INITIAL_CAPITAL - 1.0,
        "cagr": (final / INITIAL_CAPITAL) ** (1.0 / years) - 1.0,
        "max_drawdown": float(dd.min()),
        "annualized_volatility": float(daily.std(ddof=1) * np.sqrt(252)),
        "fees_eur": float(tr["fee"].sum()) if not tr.empty else 0.0,
        "trade_actions": int(len(tr)),
        "avg_open_positions": float(eq["open_positions"].mean()),
        "top_n": TOP_N,
        "lookback_days": LOOKBACK_DAYS,
        "skip_days": SKIP_DAYS,
        "variant_count": 1,
    }
    return eq, tr, metrics


def subperiod_metrics(eq: pd.DataFrame) -> list[dict]:
    periods = [("2010_2016", "2010-01-01", "2017-01-01"), ("2017_2022", "2017-01-01", "2023-01-01")]
    rows = []
    for name, a, b in periods:
        g = eq[(eq["date"] >= a) & (eq["date"] < b)].copy()
        if len(g) < 2:
            rows.append({"period": name, "status": "insufficient"})
            continue
        f, l = float(g.iloc[0].equity_eur), float(g.iloc[-1].equity_eur)
        years = max((g.iloc[-1].date - g.iloc[0].date).days / 365.2425, 1e-9)
        dd = g.equity_eur / g.equity_eur.cummax() - 1
        rows.append({"period": name, "start_equity": f, "end_equity": l, "return": l/f-1, "cagr": (l/f)**(1/years)-1, "max_drawdown": float(dd.min())})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, default=Path("outputs/v23_cross_sectional_momentum"))
    args = ap.parse_args()
    z = load_history(args.history)
    signals = build_monthly_signals(z)
    if signals.empty:
        raise SystemExit("BLOCK_V23_MOMENTUM: no signals")
    base_eq, base_tr, base = simulate(z, signals, False)
    stress_eq, stress_tr, stress = simulate(z, signals, True)
    out = args.out_dir; out.mkdir(parents=True, exist_ok=True)
    signals.to_csv(out / "MOMENTUM_SIGNALS_PRE2023.csv", index=False)
    base_eq.to_csv(out / "MOMENTUM_EQUITY_BASE_PRE2023.csv", index=False)
    stress_eq.to_csv(out / "MOMENTUM_EQUITY_STRESS_PRE2023.csv", index=False)
    base_tr.to_csv(out / "MOMENTUM_TRADES_BASE_PRE2023.csv", index=False)
    report = {
        "version": "TABPORT_V23_XSEC_MOMENTUM_1",
        "hypothesis": "classic 12-1 cross-sectional momentum; monthly top-10 equal target allocation",
        "governance": {"holdout_2023_2026_accessed": False, "variant_count": 1, "tuning": False, "survivorship_bias": True},
        "base": base,
        "stress": stress,
        "base_subperiods": subperiod_metrics(base_eq),
        "stress_subperiods": subperiod_metrics(stress_eq),
        "warnings": ["Historical universe has survivorship bias", "Price-only reconstruction; adjusted-close is absent in canonical history", "No claim of independent observations from overlapping daily rows"]
    }
    (out / "MOMENTUM_REPORT_PRE2023.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
