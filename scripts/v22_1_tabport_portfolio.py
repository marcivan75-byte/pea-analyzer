from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

CAPITAL_INITIAL = 65_000.0
LINE_TARGET = 4_500.0
MAX_ACTIVE = 12
MAX_TRADES_MONTH = 5
MAX_TRADES_YEAR = 40
FEE_BUY = 0.002
FEE_SELL = 0.002
SLIPPAGE_ROBUST = 0.001
STOP_PCT = 0.09
ANALYSIS_END = pd.Timestamp("2026-08-31")
FEATURES = ("vol_z", "drawdown_4w", "close_vs_sma200", "atr_14_pct")
V1_THRESHOLD = 0.45
V3_REGIMES = ("BULL_CALM", "BULL_VOLATILE", "BEAR_CALM", "BEAR_VOLATILE")
V3_BASE_TARGET_KEEP = 0.25443199546163664


def _features(raw: pd.DataFrame, require_label: bool) -> pd.DataFrame:
    x = pd.DataFrame(index=raw.index)
    x["vol_z"] = pd.to_numeric(raw["vol_z"], errors="coerce")
    x["drawdown_4w"] = pd.to_numeric(raw["drawdown_4w"], errors="coerce")
    close = pd.to_numeric(raw["close"], errors="coerce")
    sma200 = pd.to_numeric(raw["sma200"], errors="coerce")
    x["close_vs_sma200"] = close / sma200 - 1.0
    x["atr_14_pct"] = pd.to_numeric(raw["atr_14_pct"], errors="coerce")
    x["date"] = pd.to_datetime(raw["as_of_date"], errors="coerce")
    label = raw["hit_stop"].astype("boolean") if "hit_stop" in raw else pd.Series(pd.NA, index=raw.index, dtype="boolean")
    x["label"] = label
    valid = np.isfinite(x[list(FEATURES)]).all(axis=1) & x["date"].notna() & (sma200 > 0) & (x["atr_14_pct"] >= 0)
    if require_label:
        valid &= x["label"].notna()
    out = x.loc[valid].copy().sort_values("date", kind="stable")
    if require_label:
        out["label"] = out["label"].astype(int)
    return out


def _add_regime(x: pd.DataFrame, atr_cut: float) -> pd.DataFrame:
    out = x.copy()
    weekly = out.groupby("date", sort=False).agg(market_trend=("close_vs_sma200", "median"), market_atr=("atr_14_pct", "median"))
    out = out.join(weekly, on="date")
    bull = out["market_trend"] > 0
    calm = out["market_atr"] <= atr_cut
    out["regime"] = np.select(
        [bull & calm, bull & ~calm, ~bull & calm, ~bull & ~calm],
        list(V3_REGIMES), default="UNKNOWN"
    )
    return out


def _signal_metrics(x: pd.DataFrame, keep: np.ndarray) -> dict[str, float | int | None]:
    g = x.loc[np.asarray(keep, dtype=bool)]
    if g.empty:
        return {"mature": 0, "expectancy": None, "profit_factor": None, "stop_rate": None, "win_rate": None, "big_winner_count": 0}
    idx = g.index
    return {"mature": int(len(g)), "expectancy": None, "profit_factor": None, "stop_rate": None, "win_rate": None, "big_winner_count": 0, "index": idx}


def _build_variant_masks(train_raw: pd.DataFrame, hold_raw: pd.DataFrame) -> tuple[dict[str, pd.Series], dict[str, object]]:
    train = _features(train_raw, True)
    hold = _features(hold_raw, False)
    split = int(len(train) * 0.80)
    fit, valid = train.iloc[:split].copy(), train.iloc[split:].copy()
    if len(fit) < 1000 or len(valid) < 1000:
        raise SystemExit("BLOCK_TABPORT_MODEL: insufficient pre-2023 train/validation")

    scaler = StandardScaler().fit(fit[list(FEATURES)])
    v1 = LogisticRegression(max_iter=5000, class_weight="balanced", random_state=42)
    v1.fit(scaler.transform(fit[list(FEATURES)]), fit["label"])
    pv1 = v1.predict_proba(scaler.transform(valid[list(FEATURES)]))[:, 1]
    target_keep = float((pv1 <= V1_THRESHOLD).mean())

    hgb = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=150, l2_regularization=2.0, random_state=42)
    hgb.fit(fit[list(FEATURES)], fit["label"])
    pv2 = hgb.predict_proba(valid[list(FEATURES)])[:, 1]
    v2_threshold = float(np.quantile(pv2, target_keep))

    ph1 = v1.predict_proba(scaler.transform(hold[list(FEATURES)]))[:, 1]
    ph2 = hgb.predict_proba(hold[list(FEATURES)])[:, 1]

    # Reproduce governed MAE V3.1 threshold selection strictly on pre-2023 validation.
    atr_cut = float(fit.groupby("date")["atr_14_pct"].median().median())
    valid_r = _add_regime(valid, atr_cut)
    hold_r = _add_regime(hold, atr_cut)
    base_keep = pv2 <= v2_threshold
    valid_raw_idx = valid.index
    valid_ret = pd.to_numeric(train_raw.loc[valid_raw_idx, "forward_ret_true_26w"], errors="coerce").to_numpy()
    valid_stop = train_raw.loc[valid_raw_idx, "hit_stop"].astype("boolean").fillna(False).astype(bool).to_numpy()
    v2_big = max(int(((valid_ret >= 0.15) & base_keep).sum()), 1)

    grids = {
        "BULL_CALM": (0.00, 0.015, 0.030, 0.045, 0.060),
        "BULL_VOLATILE": (-0.015, 0.00, 0.015, 0.030, 0.045),
        "BEAR_CALM": (-0.030, -0.015, 0.00, 0.015, 0.030),
        "BEAR_VOLATILE": (-0.060, -0.030, 0.00, 0.030, 0.060),
    }
    best_score, best_th = -1e18, None
    for offs in itertools.product(*(grids[r] for r in V3_REGIMES)):
        th = {r: float(np.clip(v2_threshold + o, 0.05, 0.95)) for r, o in zip(V3_REGIMES, offs, strict=True)}
        keep = np.array([p <= th.get(r, v2_threshold) for p, r in zip(pv2, valid_r["regime"], strict=False)], dtype=bool)
        kr = float(keep.mean())
        if kr < 0.12 or kr > 0.45 or not keep.any():
            continue
        ret = valid_ret[keep]
        stop = valid_stop[keep]
        wins = ret[ret > 0]
        losses = ret[ret <= 0]
        gp, gl = float(wins.sum()), float((-losses).sum())
        pf = gp / gl if gl > 0 else 10.0
        exp = float(np.nanmean(ret))
        sr = float(stop.mean())
        wr = float((ret > 0).mean())
        big = int(((valid_ret >= 0.15) & keep).sum())
        recall = big / v2_big
        if recall < 0.95:
            continue
        score = exp + 0.012 * np.log(max(pf, 1e-9)) - 0.018 * sr + 0.010 * wr + 0.008 * min(recall, 1.10) - 0.006 * abs(kr - V3_BASE_TARGET_KEEP)
        if score > best_score:
            best_score, best_th = score, th
    if best_th is None:
        raise SystemExit("BLOCK_TABPORT_MODEL: no governed V3.1 threshold combination")

    ph31 = np.array([p <= best_th.get(r, v2_threshold) for p, r in zip(ph2, hold_r["regime"], strict=False)], dtype=bool)

    masks = {v: pd.Series(False, index=hold_raw.index) for v in ("FULL", "MAE_V1", "MAE_V2", "MAE_V3_1")}
    masks["FULL"] = hold_raw["governed_score"].notna()
    masks["MAE_V1"].loc[hold.index] = ph1 <= V1_THRESHOLD
    masks["MAE_V2"].loc[hold.index] = ph2 <= v2_threshold
    masks["MAE_V3_1"].loc[hold.index] = ph31
    meta = {"v1_threshold": V1_THRESHOLD, "v2_threshold": v2_threshold, "v3_1_thresholds": best_th, "v3_1_atr_cut": atr_cut, "holdout_used_for_tuning": False}
    return masks, meta


def _load_prices(path: Path) -> pd.DataFrame:
    p = pd.read_parquet(path)
    p.columns = [str(c).strip().lower() for c in p.columns]
    date_col = next((c for c in ("date", "market_data_date", "as_of_date") if c in p.columns), None)
    if not date_col or "isin" not in p.columns:
        raise SystemExit(f"BLOCK_TABPORT_DATA: price schema missing date/isin: {list(p.columns)}")
    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in p.columns]
    if missing:
        raise SystemExit(f"BLOCK_TABPORT_DATA: missing OHLC columns {missing}")
    p["date"] = pd.to_datetime(p[date_col], errors="coerce").dt.normalize()
    p = p[(p["date"] >= pd.Timestamp("2022-12-01")) & (p["date"] <= ANALYSIS_END)].copy()
    for c in required:
        p[c] = pd.to_numeric(p[c], errors="coerce")
    p = p.dropna(subset=["isin", "date", "open", "low", "close"])
    p = p.sort_values(["isin", "date"], kind="stable").drop_duplicates(["isin", "date"], keep="last")
    return p[["isin", "date", "open", "high", "low", "close"]]


@dataclass
class Position:
    isin: str
    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: float
    gross_notional: float
    buy_fee: float
    stop_price: float
    horizon_date: pd.Timestamp
    score: float
    last_price: float
    planned_exit_date: pd.Timestamp | None = None
    planned_exit_price: float | None = None
    planned_exit_reason: str | None = None


def _planned_exit(row: pd.Series, hist: pd.DataFrame, entry_price: float, stop_pct: float) -> tuple[pd.Timestamp | None, float | None, str | None]:
    entry = pd.Timestamp(row["entry_date"]).normalize()
    horizon = pd.to_datetime(row.get("label_end_date_26w"), errors="coerce")
    if pd.isna(horizon):
        horizon = entry + pd.Timedelta(weeks=26)
    horizon = min(pd.Timestamp(horizon).normalize(), ANALYSIS_END)
    g = hist[(hist["date"] >= entry) & (hist["date"] <= horizon)]
    if g.empty:
        return None, None, None
    stop_price = entry_price * (1.0 - stop_pct)
    for rr in g.itertuples(index=False):
        if float(rr.open) <= stop_price:
            return pd.Timestamp(rr.date), float(rr.open), "STOP_GAP"
        if float(rr.low) <= stop_price:
            return pd.Timestamp(rr.date), float(stop_price), "STOP"
    label_end = pd.to_datetime(row.get("label_end_date_26w"), errors="coerce")
    if pd.notna(label_end) and pd.Timestamp(label_end).normalize() <= ANALYSIS_END:
        gg = g[g["date"] <= pd.Timestamp(label_end).normalize()]
        if not gg.empty:
            rr = gg.iloc[-1]
            return pd.Timestamp(rr["date"]), float(rr["close"]), "HORIZON_26W"
    return None, None, None


def _period_table(curve: pd.DataFrame, variant: str, freq: str) -> pd.DataFrame:
    s = curve.set_index("date")["equity"].sort_index()
    if freq == "Y":
        periods = s.index.to_period("Y")
    else:
        periods = s.index.to_period("Q")
    rows = []
    prev_end = CAPITAL_INITIAL
    for period in sorted(periods.unique()):
        vals = s[periods == period]
        if vals.empty:
            continue
        end = float(vals.iloc[-1])
        start = float(prev_end)
        net = end - start
        ret = end / start - 1.0 if start else np.nan
        pend = period.end_time.normalize()
        status = "COMPLETE" if pend <= ANALYSIS_END else "YTD_OR_INCOMPLETE"
        rows.append({"period": str(period), "variant": variant, "status": status, "start_equity_eur": start, "end_equity_eur": end, "net_result_eur": net, "net_return_pct": ret * 100.0})
        prev_end = end
    return pd.DataFrame(rows)


def _summary(curve: pd.DataFrame, trades: pd.DataFrame, variant: str) -> dict[str, object]:
    s = curve.set_index("date")["equity"].sort_index()
    daily = s.pct_change().fillna(0.0)
    peak = s.cummax()
    dd = s / peak - 1.0
    years = max((s.index[-1] - s.index[0]).days / 365.25, 1 / 365.25)
    cagr = (float(s.iloc[-1]) / CAPITAL_INITIAL) ** (1 / years) - 1.0
    vol = float(daily.std(ddof=1) * np.sqrt(252)) if len(daily) > 1 else np.nan
    sharpe = float(daily.mean() / daily.std(ddof=1) * np.sqrt(252)) if daily.std(ddof=1) > 0 else np.nan
    downside = daily[daily < 0]
    sortino = float(daily.mean() / downside.std(ddof=1) * np.sqrt(252)) if len(downside) > 1 and downside.std(ddof=1) > 0 else np.nan
    closed = trades[trades["status"] == "CLOSED"].copy() if not trades.empty else trades.copy()
    if not closed.empty:
        pnl = closed["net_pnl_eur"].astype(float)
        ret = closed["net_return_pct"].astype(float) / 100.0
        wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
        avg_win = float(ret[pnl > 0].mean()) if len(wins) else np.nan
        avg_loss = float(ret[pnl <= 0].mean()) if len(losses) else np.nan
        pf = float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 0 else np.nan
        rr = float(avg_win / abs(avg_loss)) if np.isfinite(avg_win) and np.isfinite(avg_loss) and avg_loss != 0 else np.nan
    else:
        pnl = pd.Series(dtype=float); ret = pd.Series(dtype=float); wins = losses = pnl; avg_win = avg_loss = pf = rr = np.nan
    return {
        "variant": variant,
        "initial_capital_eur": CAPITAL_INITIAL,
        "final_equity_eur": float(s.iloc[-1]),
        "net_result_eur": float(s.iloc[-1] - CAPITAL_INITIAL),
        "total_return_pct": float((s.iloc[-1] / CAPITAL_INITIAL - 1.0) * 100.0),
        "cagr_pct": float(cagr * 100.0),
        "max_drawdown_pct": float(dd.min() * 100.0),
        "annualized_volatility_pct": float(vol * 100.0) if np.isfinite(vol) else None,
        "sharpe_rf0": sharpe if np.isfinite(sharpe) else None,
        "sortino_rf0": sortino if np.isfinite(sortino) else None,
        "trades_opened": int(len(trades)),
        "trades_closed": int(len(closed)),
        "wins": int((pnl > 0).sum()) if len(pnl) else 0,
        "losses": int((pnl <= 0).sum()) if len(pnl) else 0,
        "win_rate_pct": float((pnl > 0).mean() * 100.0) if len(pnl) else None,
        "false_positives_stop": int(closed["exit_reason"].astype(str).str.startswith("STOP").sum()) if len(closed) else 0,
        "false_positive_rate_pct": float(closed["exit_reason"].astype(str).str.startswith("STOP").mean() * 100.0) if len(closed) else None,
        "avg_win_pct": avg_win * 100.0 if np.isfinite(avg_win) else None,
        "avg_loss_pct": avg_loss * 100.0 if np.isfinite(avg_loss) else None,
        "rr_payoff": rr if np.isfinite(rr) else None,
        "profit_factor": pf if np.isfinite(pf) else None,
        "expectancy_eur_per_closed_trade": float(pnl.mean()) if len(pnl) else None,
        "fees_eur": float(trades["fees_eur"].sum()) if len(trades) else 0.0,
        "slippage_eur": float(trades["slippage_eur"].sum()) if len(trades) else 0.0,
        "avg_holding_days": float(closed["holding_days"].mean()) if len(closed) else None,
        "best_trade_eur": float(pnl.max()) if len(pnl) else None,
        "worst_trade_eur": float(pnl.min()) if len(pnl) else None,
        "avg_active_lines": float(curve["active_lines"].mean()),
        "max_active_lines": int(curve["active_lines"].max()),
        "avg_capital_utilization_pct": float(curve["invested_value"].div(curve["equity"]).replace([np.inf, -np.inf], np.nan).fillna(0).mean() * 100.0),
        "avg_cash_eur": float(curve["cash"].mean()),
    }


def simulate(hold_raw: pd.DataFrame, prices: pd.DataFrame, eligible: pd.Series, variant: str, slippage: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates = hold_raw.loc[eligible].copy()
    candidates["entry_date"] = pd.to_datetime(candidates["entry_date"], errors="coerce").dt.normalize()
    candidates["governed_score"] = pd.to_numeric(candidates["governed_score"], errors="coerce")
    candidates["entry_price"] = pd.to_numeric(candidates["entry_price"], errors="coerce")
    candidates = candidates.dropna(subset=["isin", "entry_date", "entry_price", "governed_score"])
    candidates = candidates[(candidates["entry_date"] >= pd.Timestamp("2023-01-01")) & (candidates["entry_date"] <= ANALYSIS_END)]
    candidates = candidates.sort_values(["entry_date", "governed_score"], ascending=[True, False], kind="stable")

    by_isin = {k: g.reset_index(drop=True) for k, g in prices.groupby("isin", sort=False)}
    quote = prices.set_index(["date", "isin"])[["open", "low", "close"]].sort_index()
    cand_by_date = {d: g for d, g in candidates.groupby("entry_date", sort=True)}
    all_dates = pd.DatetimeIndex(sorted(set(prices["date"].unique()).union(set(cand_by_date.keys()))))
    all_dates = all_dates[(all_dates >= pd.Timestamp("2023-01-01")) & (all_dates <= ANALYSIS_END)]

    cash = CAPITAL_INITIAL
    active: dict[str, Position] = {}
    monthly_count: dict[str, int] = {}
    yearly_count: dict[int, int] = {}
    trade_records: list[dict[str, object]] = []
    curve_rows: list[dict[str, object]] = []

    for day in all_dates:
        # Entries happen at the open using only signals already known; positions exiting later today still consume a slot at the open.
        g = cand_by_date.get(pd.Timestamp(day))
        if g is not None and len(active) < MAX_ACTIVE:
            month_key = pd.Timestamp(day).strftime("%Y-%m")
            year = int(pd.Timestamp(day).year)
            used_m = monthly_count.get(month_key, 0)
            used_y = yearly_count.get(year, 0)
            for _, row in g.iterrows():
                if len(active) >= MAX_ACTIVE or used_m >= MAX_TRADES_MONTH or used_y >= MAX_TRADES_YEAR:
                    break
                isin = str(row["isin"])
                if isin in active or isin not in by_isin:
                    continue
                px0 = float(row["entry_price"])
                execution_px = px0 * (1.0 + slippage)
                max_notional = max(cash / (1.0 + FEE_BUY), 0.0)
                notional = min(LINE_TARGET, max_notional)
                if notional < 100.0:
                    continue
                shares = notional / execution_px
                buy_fee = notional * FEE_BUY
                cash -= notional + buy_fee
                stop_pct = float(pd.to_numeric(row.get("stop_pct_used", STOP_PCT), errors="coerce"))
                if not np.isfinite(stop_pct) or stop_pct <= 0:
                    stop_pct = STOP_PCT
                exd, expx_raw, reason = _planned_exit(row, by_isin[isin], px0, stop_pct)
                expx = expx_raw * (1.0 - slippage) if expx_raw is not None else None
                horizon = pd.to_datetime(row.get("label_end_date_26w"), errors="coerce")
                if pd.isna(horizon):
                    horizon = pd.Timestamp(day) + pd.Timedelta(weeks=26)
                ticker = str(row.get("ticker", isin))
                pos = Position(isin, ticker, pd.Timestamp(day), execution_px, shares, notional, buy_fee, px0 * (1-stop_pct), pd.Timestamp(horizon).normalize(), float(row["governed_score"]), execution_px, exd, expx, reason)
                active[isin] = pos
                monthly_count[month_key] = used_m = used_m + 1
                yearly_count[year] = used_y = used_y + 1
                trade_records.append({"variant": variant, "isin": isin, "ticker": ticker, "entry_date": day, "entry_price": execution_px, "gross_notional_eur": notional, "buy_fee_eur": buy_fee, "score": float(row["governed_score"]), "planned_exit_date": exd, "status": "OPEN", "exit_date": pd.NaT, "exit_price": np.nan, "exit_reason": "OPEN", "sell_fee_eur": 0.0, "fees_eur": buy_fee, "slippage_eur": notional * slippage, "net_pnl_eur": np.nan, "net_return_pct": np.nan, "holding_days": np.nan})

        # Update quotes and process intraday/horizon exits.
        to_close: list[str] = []
        for isin, pos in list(active.items()):
            try:
                q = quote.loc[(pd.Timestamp(day), isin)]
                if isinstance(q, pd.DataFrame):
                    q = q.iloc[-1]
                pos.last_price = float(q["close"])
            except KeyError:
                pass
            if pos.planned_exit_date is not None and pd.Timestamp(day) == pos.planned_exit_date:
                sale_px = float(pos.planned_exit_price)
                proceeds = pos.shares * sale_px
                sell_fee = proceeds * FEE_SELL
                cash += proceeds - sell_fee
                for rec in reversed(trade_records):
                    if rec["isin"] == isin and rec["status"] == "OPEN":
                        rec["status"] = "CLOSED"
                        rec["exit_date"] = day
                        rec["exit_price"] = sale_px
                        rec["exit_reason"] = pos.planned_exit_reason
                        rec["sell_fee_eur"] = sell_fee
                        rec["fees_eur"] = pos.buy_fee + sell_fee
                        rec["slippage_eur"] = float(rec["slippage_eur"]) + proceeds * slippage
                        rec["net_pnl_eur"] = proceeds - sell_fee - pos.gross_notional - pos.buy_fee
                        rec["net_return_pct"] = rec["net_pnl_eur"] / (pos.gross_notional + pos.buy_fee) * 100.0
                        rec["holding_days"] = int((pd.Timestamp(day) - pos.entry_date).days)
                        break
                to_close.append(isin)
        for isin in to_close:
            del active[isin]

        invested = sum(p.shares * p.last_price for p in active.values())
        equity = cash + invested
        curve_rows.append({"date": day, "variant": variant, "cash": cash, "invested_value": invested, "equity": equity, "active_lines": len(active)})

    trades = pd.DataFrame(trade_records)
    if not trades.empty:
        for i, rec in trades[trades["status"] == "OPEN"].iterrows():
            isin = str(rec["isin"])
            if isin in active:
                p = active[isin]
                mtm = p.shares * p.last_price
                hypothetical_sell_fee = mtm * FEE_SELL
                trades.at[i, "net_pnl_eur"] = mtm - hypothetical_sell_fee - p.gross_notional - p.buy_fee
                trades.at[i, "net_return_pct"] = trades.at[i, "net_pnl_eur"] / (p.gross_notional + p.buy_fee) * 100.0
                trades.at[i, "exit_reason"] = "OPEN_MTM"
                trades.at[i, "fees_eur"] = p.buy_fee
                trades.at[i, "holding_days"] = int((ANALYSIS_END - p.entry_date).days)
    return pd.DataFrame(curve_rows), trades


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--price-parquet", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    train_raw = pd.read_csv(args.input_dir / "V22_1_TRAIN.csv", low_memory=False)
    hold_raw = pd.read_csv(args.input_dir / "V22_1_HOLDOUT.csv", low_memory=False)
    masks, model_meta = _build_variant_masks(train_raw, hold_raw)
    prices = _load_prices(args.price_parquet)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    summaries, annuals, quarters, all_trades, robustness = [], [], [], [], []
    for variant in ("FULL", "MAE_V1", "MAE_V2", "MAE_V3_1"):
        curve, trades = simulate(hold_raw, prices, masks[variant], variant, slippage=0.0)
        if curve.empty:
            raise SystemExit(f"BLOCK_TABPORT_SIM: empty curve for {variant}")
        summaries.append(_summary(curve, trades, variant))
        annuals.append(_period_table(curve, variant, "Y"))
        quarters.append(_period_table(curve, variant, "Q"))
        all_trades.append(trades)
        curve.to_csv(args.out_dir / f"TABPORT_EQUITY_{variant}.csv", index=False)

        curve_r, trades_r = simulate(hold_raw, prices, masks[variant], variant, slippage=SLIPPAGE_ROBUST)
        rr = _summary(curve_r, trades_r, variant)
        rr["scenario"] = "ROBUST_PLUS_0_10PCT_SLIPPAGE_EACH_SIDE"
        robustness.append(rr)

    summary = pd.DataFrame(summaries)
    annual = pd.concat(annuals, ignore_index=True)
    quarter = pd.concat(quarters, ignore_index=True)
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    robust = pd.DataFrame(robustness)

    summary.to_csv(args.out_dir / "TABPORT_SUMMARY.csv", index=False)
    annual.to_csv(args.out_dir / "TABPORT_ANNUAL.csv", index=False)
    quarter.to_csv(args.out_dir / "TABPORT_QUARTERLY.csv", index=False)
    trades.to_csv(args.out_dir / "TABPORT_TRADES.csv", index=False)
    robust.to_csv(args.out_dir / "TABPORT_ROBUSTNESS.csv", index=False)

    config = {
        "name": "TABPORT",
        "capital_initial_eur": CAPITAL_INITIAL,
        "line_target_max_eur": LINE_TARGET,
        "max_active_lines": MAX_ACTIVE,
        "max_entries_per_calendar_month": MAX_TRADES_MONTH,
        "max_entries_per_calendar_year": MAX_TRADES_YEAR,
        "buy_fee_rate": FEE_BUY,
        "sell_fee_rate": FEE_SELL,
        "cash_yield": 0.0,
        "same_isin_concurrent_positions": False,
        "reentry_after_exit": True,
        "selection": "CHRONOLOGICAL_SIGNAL_AVAILABILITY_THEN_GOVERNED_SCORE_DESC_WITHIN_SAME_ENTRY_DATE; NO_MONTH_END_LOOKAHEAD",
        "entry": "NEXT_SESSION_OPEN_J1_FROM_GOVERNED_LEDGER",
        "stop": "9PCT_INTRADAY; GAP_THROUGH_STOP_EXECUTES_AT_OPEN",
        "max_holding": "26W_GOVERNED_LABEL_END",
        "mark_to_market": "DAILY_CLOSE",
        "base_slippage": 0.0,
        "robustness_slippage_each_side": SLIPPAGE_ROBUST,
        "variants": ["FULL", "MAE_V1", "MAE_V2", "MAE_V3_1"],
        "model_governance": model_meta,
        "economic_result_definition": "TRUE_CAPITAL_CONSTRAINED_MARK_TO_MARKET_PORTFOLIO_RETURN_WITH_FEES",
        "holdout_used_for_model_tuning": False,
        "survivorship_bias": True,
    }
    (args.out_dir / "TABPORT_CONFIG.json").write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    print("TABPORT SUMMARY")
    print(summary.to_string(index=False))
    print("TABPORT ANNUAL")
    print(annual.to_string(index=False))
    print("TABPORT QUARTERLY")
    print(quarter.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
