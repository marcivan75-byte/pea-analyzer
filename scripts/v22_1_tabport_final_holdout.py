from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.v22_1_portfolio_robustness_pass5 import (
    INITIAL_CAPITAL,
    STRESS_SLIPPAGE_SIDE,
    daily_mtm_equity,
    metrics as portfolio_metrics,
    simulate,
)

BIG_WIN = 0.15
TARGET_WINDOWS = (20, 63, 126)


def n(s):
    return pd.to_numeric(s, errors="coerce")


def build_pit(df: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    x["date"] = pd.to_datetime(df["as_of_date"], errors="coerce").dt.normalize()
    x["ticker"] = df["ticker"].astype(str)
    x["isin"] = df["isin"].astype(str)
    x["gov"] = n(df["governed_score"])
    mom, dd, atr = n(df["mom_26w"]), n(df["drawdown_4w"]), n(df["atr_14_pct"])
    rsi, close, sma200 = n(df["rsi_14_hebdo"]), n(df["close"]), n(df["sma200"])
    trend = close / sma200 - 1.0
    eps = 0.01
    x["H_MOM_VOL"] = mom / (atr.abs() + eps)
    x["H_TREND_DD"] = trend - dd.abs()
    x["H_RSI_TREND"] = ((rsi - 50.0) / 25.0) * trend
    x["H_VOL_DD"] = atr * dd.abs()
    x["H_MOM_DD"] = mom * (1.0 - dd.abs())
    x["H_TREND_VOL"] = trend / (atr.abs() + eps)
    x["H_OPPORTUNITY_RISK"] = mom / (atr.abs() + dd.abs() + eps)
    x["trend"] = trend
    x["atr"] = atr
    good = x["date"].notna() & np.isfinite(x["gov"])
    for c in ["H_MOM_VOL", "H_TREND_DD", "H_RSI_TREND", "H_VOL_DD", "H_MOM_DD", "H_TREND_VOL", "H_OPPORTUNITY_RISK", "trend", "atr"]:
        good &= np.isfinite(x[c])
    return x.loc[good].sort_values(["date", "ticker", "isin"], kind="stable")


def cap(g: pd.DataFrame, col: str, max_date: int, max_month: int, max_year: int) -> pd.DataFrame:
    out, mc, yc = [], {}, {}
    for d, grp in g.sort_values(["date", "ticker"], kind="stable").groupby("date", sort=True):
        mo, yr = d.to_period("M"), int(d.year)
        rem = min(max_month - mc.get(mo, 0), max_year - yc.get(yr, 0))
        if rem <= 0:
            continue
        z = grp.dropna(subset=[col]).sort_values([col, "ticker", "isin"], ascending=[False, True, True], kind="stable").head(min(max_date, rem))
        if len(z):
            out.append(z)
            mc[mo] = mc.get(mo, 0) + len(z)
            yc[yr] = yc.get(yr, 0) + len(z)
    return pd.concat(out).sort_values(["date", col, "ticker"], ascending=[True, False, True], kind="stable") if out else g.iloc[:0].copy()


def load_rr_targets(price_path: Path) -> pd.DataFrame:
    p = pd.read_parquet(price_path)
    p.columns = [str(c).strip().lower() for c in p.columns]
    dc = next((c for c in ("date", "market_data_date", "as_of_date") if c in p.columns), None)
    if dc is None or "isin" not in p.columns or "high" not in p.columns:
        raise SystemExit("BLOCK_FINAL_RR: governed OHLC lacks isin/date/high")
    p["date"] = pd.to_datetime(p[dc], errors="coerce").dt.normalize()
    p["high"] = n(p["high"])
    p = p.dropna(subset=["isin", "date", "high"]).sort_values(["isin", "date"], kind="stable").drop_duplicates(["isin", "date"], keep="last")
    mins = {20: 10, 63: 20, 126: 40}
    for w in TARGET_WINDOWS:
        p[f"prior_high_{w}s"] = p.groupby("isin", sort=False)["high"].transform(lambda s, w=w: s.shift(1).rolling(w, min_periods=mins[w]).max())
    return p[["isin", "date"] + [f"prior_high_{w}s" for w in TARGET_WINDOWS]]


def attach_rr(df: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["_row"] = np.arange(len(x))
    left = x.sort_values(["date", "isin"], kind="stable")
    right = hist.sort_values(["date", "isin"], kind="stable")
    m = pd.merge_asof(left, right, on="date", by="isin", direction="backward", allow_exact_matches=True)
    cols = [f"prior_high_{w}s" for w in TARGET_WINDOWS]
    x = x.merge(m[["_row"] + cols], on="_row", how="left", validate="one_to_one").drop(columns="_row")
    close = n(x["close"])
    stop_pct = n(x["stop_pct_used"]).where(n(x["stop_pct_used"]) > 0, 0.09)
    cand = pd.DataFrame(index=x.index)
    for w in TARGET_WINDOWS:
        t = n(x[f"prior_high_{w}s"])
        cand[str(w)] = t.where(np.isfinite(t) & (t > close))
    target = cand.min(axis=1, skipna=True)
    x["rr_ex_ante"] = (target / close - 1.0) / stop_pct
    return x


def outcome_metrics(g: pd.DataFrame) -> dict:
    z = g[g["forward_ret_true_26w"].notna() & g["hit_stop"].notna()].copy()
    if z.empty:
        return {"n": 0}
    r = n(z["forward_ret_true_26w"])
    w, l = r[r > 0], r[r <= 0]
    gp, gl = float(w.sum()), float((-l).sum())
    mae = n(z["mae"]) if "mae" in z else pd.Series(dtype=float)
    mfe = n(z["mfe"]) if "mfe" in z else pd.Series(dtype=float)
    rr = n(z["rr_ex_ante"]) if "rr_ex_ante" in z else pd.Series(dtype=float)
    return {
        "n": int(len(z)),
        "gains": int((r > 0).sum()),
        "pertes": int((r <= 0).sum()),
        "win_rate": float((r > 0).mean()),
        "expectancy": float(r.mean()),
        "profit_factor": float(gp / gl) if gl > 0 else None,
        "payoff_ratio": float(w.mean() / abs(l.mean())) if len(w) and len(l) and l.mean() != 0 else None,
        "stops_false_positives": int(z["hit_stop"].astype(bool).sum()),
        "stop_rate": float(z["hit_stop"].astype(bool).mean()),
        "big_winners_ge_15pct": int((r >= BIG_WIN).sum()),
        "avg_gain": float(w.mean()) if len(w) else None,
        "avg_loss": float(l.mean()) if len(l) else None,
        "mae_mean": float(mae.mean()) if len(mae) else None,
        "mae_median": float(mae.median()) if len(mae) else None,
        "mfe_mean": float(mfe.mean()) if len(mfe) else None,
        "mfe_median": float(mfe.median()) if len(mfe) else None,
        "rr_ex_ante_mean": float(rr.mean()) if rr.notna().any() else None,
        "rr_ex_ante_median": float(rr.median()) if rr.notna().any() else None,
        "rr_ex_ante_gt_3_3_rate": float((rr.dropna() > 3.3).mean()) if rr.notna().any() else None,
    }


def period_portfolio(equity: pd.DataFrame, trades: pd.DataFrame, freq: str) -> pd.DataFrame:
    e = equity.copy()
    e["date"] = pd.to_datetime(e["date"])
    e["period"] = e["date"].dt.to_period(freq)
    ends = e.groupby("period", sort=True).tail(1)[["period", "date", "equity_eur", "capital_utilization"]].copy()
    prev = INITIAL_CAPITAL
    rows = []
    t = trades.copy(); t["exit_date"] = pd.to_datetime(t["exit_date"]); t["period"] = t["exit_date"].dt.to_period(freq)
    for _, r in ends.iterrows():
        end = float(r["equity_eur"])
        p = r["period"]
        tt = t[t["period"] == p]
        rows.append({
            "period": str(p),
            "end_date": str(pd.Timestamp(r["date"]).date()),
            "net_eur": float(end - prev),
            "net_pct": float(end / prev - 1.0) if prev else None,
            "end_equity_eur": end,
            "trades_closed": int(len(tt)),
            "wins": int((tt["net_pnl_eur"] > 0).sum()) if len(tt) else 0,
            "losses": int((tt["net_pnl_eur"] <= 0).sum()) if len(tt) else 0,
            "stops": int(tt["hit_stop"].astype(bool).sum()) if len(tt) else 0,
            "fees_eur": float(tt["fees_eur"].sum()) if len(tt) else 0.0,
            "capital_utilization_end": float(r["capital_utilization"]),
        })
        prev = end
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", type=Path, required=True)
    ap.add_argument("--pass6-report", type=Path, required=True)
    ap.add_argument("--frozen-config", type=Path, required=True)
    ap.add_argument("--price-parquet", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()

    p6 = json.loads(a.pass6_report.read_text(encoding="utf-8"))
    cfg = json.loads(a.frozen_config.read_text(encoding="utf-8"))
    if p6.get("governance", {}).get("holdout_accessed") is not False or p6.get("selected", {}).get("variant") != cfg.get("variant"):
        raise SystemExit("BLOCK_FINAL_GOVERNANCE: pass6 not frozen")
    if cfg.get("variant") != "ADAPT" or cfg.get("holdout_accessed_during_selection") is not False:
        raise SystemExit("BLOCK_FINAL_GOVERNANCE: unsupported/nonsealed final variant")

    raw = pd.read_csv(a.holdout, low_memory=False)
    raw["date"] = pd.to_datetime(raw["as_of_date"], errors="coerce").dt.normalize()
    max_observed = raw["date"].max()
    maturity_cutoff = (max_observed - pd.Timedelta(weeks=26)).normalize()
    raw_eval = raw[raw["date"] <= maturity_cutoff].copy()
    pit = build_pit(raw_eval)

    f = cfg["pass2_filter"]
    criteria, keep, directions = list(f["criteria"]), float(f["keep_level"]), f["risk_directions"]
    good_parts = []
    for c in criteria:
        direction = int(directions.get(c, {}).get("risk_direction", 1))
        rank = pit.groupby("date")[c].rank(method="average", pct=True, ascending=True)
        good_parts.append(rank if direction < 0 else (1.0 - rank))
    pit = pit.loc[pd.concat(good_parts, axis=1).mean(axis=1) >= (1.0 - keep)].copy()

    pit["pgov"] = pit.groupby("date")["gov"].rank(method="average", pct=True)
    pit["pvold_good"] = 1.0 - pit.groupby("date")["H_VOL_DD"].rank(method="average", pct=True)
    base_w = float(cfg["pass3_static_antistop_weight"])
    th = cfg["regime_thresholds"]
    wr = np.clip(base_w + 0.10 * (pit["trend"] <= float(th["trend_q33"])) + 0.10 * (pit["atr"] >= float(th["atr_q67"])), base_w, 0.80)
    pit["ADAPT"] = (1.0 - wr) * pit["pgov"] + wr * pit["pvold_good"]
    cp = cfg["capacity"]
    selected = cap(pit, "ADAPT", int(cp["max_per_signal_date"]), int(cp["max_entries_month"]), int(cp["max_entries_year"]))
    if selected.empty:
        raise SystemExit("BLOCK_FINAL_SELECTION: no frozen holdout selections")

    keys = ["date", "ticker", "isin"]
    cols = ["date", "ticker", "isin", "close", "stop_pct_used", "entry_date", "entry_price", "forward_ret_true_26w", "label_end_date_26w", "hit_stop", "day_stop", "mae", "mfe"]
    merged = selected[keys + ["ADAPT"]].merge(raw_eval[cols], on=keys, how="left", validate="one_to_one")
    if merged[["entry_date", "entry_price", "forward_ret_true_26w", "label_end_date_26w", "hit_stop"]].isna().any().any():
        raise SystemExit("BLOCK_FINAL_MATURITY: selected supposedly matured rows lack labels")
    merged = attach_rr(merged, load_rr_targets(a.price_parquet))
    merged["rank_score"] = n(merged["ADAPT"])
    merged["entry_date"] = pd.to_datetime(merged["entry_date"])
    merged["label_end_date_26w"] = pd.to_datetime(merged["label_end_date_26w"])

    base_t, _, base_diag = simulate(merged, 0.0)
    stress_t, _, stress_diag = simulate(merged, STRESS_SLIPPAGE_SIDE)
    base_e = daily_mtm_equity(base_t, a.price_parquet)
    stress_e = daily_mtm_equity(stress_t, a.price_parquet)
    base_m = portfolio_metrics(base_t, base_e, INITIAL_CAPITAL)
    stress_m = portfolio_metrics(stress_t, stress_e, INITIAL_CAPITAL)

    global_out = outcome_metrics(merged)
    annual_signal = []
    quarterly_signal = []
    for y, g in merged.groupby(merged["date"].dt.year, sort=True):
        annual_signal.append({"period": str(int(y)), **outcome_metrics(g)})
    for q, g in merged.groupby(merged["date"].dt.to_period("Q"), sort=True):
        quarterly_signal.append({"period": str(q), **outcome_metrics(g)})
    annual_port = period_portfolio(base_e, base_t, "Y")
    quarter_port = period_portfolio(base_e, base_t, "Q")
    annual = annual_port.merge(pd.DataFrame(annual_signal), on="period", how="outer", suffixes=("_portfolio", "_signal"))
    quarterly = quarter_port.merge(pd.DataFrame(quarterly_signal), on="period", how="outer", suffixes=("_portfolio", "_signal"))

    complete_years = annual[annual["period"].isin(["2023", "2024", "2025"])] if len(annual) else annual
    all_complete_years_gt15 = bool(len(complete_years) == 3 and (pd.to_numeric(complete_years["net_pct"], errors="coerce") > 0.15).all())
    rr_med = global_out.get("rr_ex_ante_median")
    objectives = {
        "rr_gt_3_3": {"target": 3.3, "median_rr_ex_ante": rr_med, "met_on_median": bool(rr_med is not None and rr_med > 3.3), "share_selected_rr_gt_3_3": global_out.get("rr_ex_ante_gt_3_3_rate")},
        "net_annual_gt_15pct": {"target": 0.15, "base_cagr": base_m.get("cagr"), "cagr_gt_15pct": bool(base_m.get("cagr") is not None and base_m["cagr"] > 0.15), "all_complete_2023_2025_years_gt_15pct": all_complete_years_gt15},
    }

    report = {
        "version": "TABPORT_V22_1_FINAL_HOLDOUT_1_FROZEN",
        "governance": {
            "holdout_accessed": True,
            "holdout_use": "FINAL_FROZEN_EVALUATION_ONLY_NO_TUNING",
            "frozen_variant": cfg["variant"],
            "signal_maturity_rule": "signal_date <= max_holdout_signal_date - 26 weeks",
            "max_holdout_signal_date": str(max_observed.date()),
            "evaluation_signal_cutoff": str(maturity_cutoff.date()),
            "survivorship_bias_disclosure": "Historical results remain subject to survivorship bias unless the source universe itself is point-in-time; no claim of elimination is made.",
            "mae_mfe_use": "REPORTING_ONLY_AFTER_SELECTION_NOT_USED_FOR_SIGNAL_OR_TUNING",
        },
        "global_signal_metrics": global_out,
        "portfolio_base": base_m,
        "portfolio_stress": stress_m,
        "robustness": {
            "cagr_delta_stress": float(stress_m["cagr"] - base_m["cagr"]),
            "net_eur_delta_stress": float(stress_m["net_eur"] - base_m["net_eur"]),
            "pf_ratio_stress_to_base": float(stress_m["profit_factor"] / base_m["profit_factor"]) if base_m.get("profit_factor") and stress_m.get("profit_factor") else None,
            "slippage_dependency_flag": bool(base_m["net_eur"] > 0 and stress_m["net_eur"] <= 0),
        },
        "diagnostics": {"base": base_diag, "stress": stress_diag},
        "objectives": objectives,
        "annual_file": "TABPORT_FINAL_ANNUAL.csv",
        "quarterly_file": "TABPORT_FINAL_QUARTERLY.csv",
        "trades_file": "TABPORT_FINAL_SELECTED_HOLDOUT.csv",
    }
    out = a.out_dir; out.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out / "TABPORT_FINAL_SELECTED_HOLDOUT.csv", index=False)
    base_t.to_csv(out / "TABPORT_FINAL_TRADES_BASE.csv", index=False)
    stress_t.to_csv(out / "TABPORT_FINAL_TRADES_STRESS.csv", index=False)
    base_e.to_csv(out / "TABPORT_FINAL_EQUITY_BASE.csv", index=False)
    stress_e.to_csv(out / "TABPORT_FINAL_EQUITY_STRESS.csv", index=False)
    annual.to_csv(out / "TABPORT_FINAL_ANNUAL.csv", index=False)
    quarterly.to_csv(out / "TABPORT_FINAL_QUARTERLY.csv", index=False)
    (out / "TABPORT_FINAL_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    md = ["# TABPORT / TAB1 — final frozen holdout", "", f"Evaluation signals through **{maturity_cutoff.date()}**; no 2023-2026 tuning.", "", "## Global", "", "```json", json.dumps({"signal": global_out, "portfolio_base": base_m, "portfolio_stress": stress_m, "objectives": objectives}, indent=2, sort_keys=True), "```", "", "Annual and quarterly detail: `TABPORT_FINAL_ANNUAL.csv`, `TABPORT_FINAL_QUARTERLY.csv`."]
    (out / "TABPORT_FINAL_REPORT.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
