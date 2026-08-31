from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

HOLDOUT_START = pd.Timestamp("2023-01-01")
STOP_DEFAULT = 0.09
TOP_K = (1, 3, 5)
BIG_WIN = 0.15
TARGET_LOOKBACK_SESSIONS = 126


def n(s):
    return pd.to_numeric(s, errors="coerce")


def load_prior_high_targets(price_path: Path) -> pd.DataFrame:
    p = pd.read_parquet(price_path)
    p.columns = [str(c).strip().lower() for c in p.columns]
    date_col = next((c for c in ("date", "market_data_date", "as_of_date") if c in p.columns), None)
    if date_col is None or "isin" not in p.columns or "high" not in p.columns:
        raise SystemExit("BLOCK_RR_TARGET_DATA: governed OHLC history lacks isin/date/high")
    p["date"] = pd.to_datetime(p[date_col], errors="coerce").dt.normalize()
    p["high"] = n(p["high"])
    p = p.dropna(subset=["isin", "date", "high"])
    p = p.sort_values(["isin", "date"], kind="stable").drop_duplicates(["isin", "date"], keep="last")
    # Strict PIT target: max HIGH over the 126 PRIOR trading sessions. shift(1)
    # guarantees that the signal day's own high never enters its target.
    p["prior_high_126s"] = (
        p.groupby("isin", sort=False)["high"]
        .transform(lambda s: s.shift(1).rolling(TARGET_LOOKBACK_SESSIONS, min_periods=40).max())
    )
    return p[["isin", "date", "prior_high_126s"]].dropna(subset=["prior_high_126s"])


def attach_target(raw: pd.DataFrame, target_hist: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x["date"] = pd.to_datetime(x["as_of_date"], errors="coerce").dt.normalize()
    x["_row_id"] = np.arange(len(x))
    left = x.dropna(subset=["isin", "date"]).sort_values(["date", "isin"], kind="stable")
    right = target_hist.sort_values(["date", "isin"], kind="stable")
    # Backward as-of lookup; each target itself was already shifted one session.
    merged = pd.merge_asof(
        left,
        right,
        on="date",
        by="isin",
        direction="backward",
        allow_exact_matches=True,
    )
    out = x.merge(merged[["_row_id", "prior_high_126s"]], on="_row_id", how="left", validate="one_to_one")
    return out.drop(columns=["_row_id"])


def add_features(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x["date"] = pd.to_datetime(x["as_of_date"], errors="coerce").dt.normalize()
    close = n(x["close"])
    atr = n(x["atr_14_pct"])
    mom = n(x["mom_26w"])
    dd = n(x["drawdown_4w"])
    rsi = n(x["rsi_14_hebdo"])
    sma200 = n(x["sma200"])
    trend = close / sma200 - 1.0
    stop_pct = n(x["stop_pct_used"]) if "stop_pct_used" in x else pd.Series(STOP_DEFAULT, index=x.index)
    stop_pct = stop_pct.where(np.isfinite(stop_pct) & (stop_pct > 0), STOP_DEFAULT)
    target = n(x["prior_high_126s"])

    # Ex-ante RR at signal close. No future entry price, MFE or forward return is used.
    upside = target / close - 1.0
    x["rr_ex_ante"] = upside / stop_pct
    x["h_mom_vol"] = mom / (atr.abs() + 0.01)
    x["h_trend_dd"] = trend - dd.abs()
    x["h_opportunity_risk"] = mom / (atr.abs() + dd.abs() + 0.01)
    x["h_rsi_trend"] = ((rsi - 50.0) / 25.0) * trend
    x["ret26"] = n(x["forward_ret_true_26w"])
    x["stop"] = x["hit_stop"].astype("boolean")
    return x


def pct_rank_by_date(df: pd.DataFrame, col: str, higher=True) -> pd.Series:
    return df.groupby("date")[col].rank(pct=True, ascending=higher is False, method="average")


def metrics(g: pd.DataFrame) -> dict:
    g = g[g["ret26"].notna() & g["stop"].notna()]
    if g.empty:
        return {"n": 0}
    r = g["ret26"].astype(float)
    w, l = r[r > 0], r[r <= 0]
    gp, gl = float(w.sum()), float((-l).sum())
    return {
        "n": int(len(g)),
        "win_rate": float((r > 0).mean()),
        "stop_rate": float(g["stop"].astype(bool).mean()),
        "expectancy": float(r.mean()),
        "profit_factor": float(gp / gl) if gl > 0 else None,
        "payoff_ratio": float(w.mean() / abs(l.mean())) if len(w) and len(l) and l.mean() != 0 else None,
        "big_winner_rate": float((r >= BIG_WIN).mean()),
        "mean_rr_ex_ante": float(g["rr_ex_ante"].mean()) if "rr_ex_ante" in g else None,
    }


def evaluate(df: pd.DataFrame, score_col: str, label: str) -> list[dict]:
    rows = []
    z = df.dropna(subset=["date", score_col, "ret26", "stop"]).copy()
    z = z.sort_values(["date", score_col], ascending=[True, False], kind="stable")
    for k in TOP_K:
        top = z.groupby("date", sort=False).head(k)
        rows.append({"ranking": label, "top_k_per_signal_date": k, **metrics(top)})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--price-parquet", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    targets = load_prior_high_targets(args.price_parquet)
    train_raw = attach_target(pd.read_csv(args.input_dir / "V22_1_TRAIN.csv", low_memory=False), targets)
    hold_raw = attach_target(pd.read_csv(args.input_dir / "V22_1_HOLDOUT.csv", low_memory=False), targets)
    train = add_features(train_raw)
    hold = add_features(hold_raw)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    coverage_train = float(train["rr_ex_ante"].notna().mean())
    coverage_hold = float(hold["rr_ex_ante"].notna().mean())
    if coverage_train < 0.80 or coverage_hold < 0.80:
        raise SystemExit(f"BLOCK_RR_TARGET_COVERAGE: train={coverage_train:.4f} hold={coverage_hold:.4f}")

    train = train[train["date"] < HOLDOUT_START].copy()
    split = int(len(train) * 0.80)
    valid = train.iloc[split:].copy()
    for df in (valid, hold):
        df["p_rr"] = pct_rank_by_date(df, "rr_ex_ante", True)
        df["p_momvol"] = pct_rank_by_date(df, "h_mom_vol", True)
        df["p_trenddd"] = pct_rank_by_date(df, "h_trend_dd", True)
        df["p_opp"] = pct_rank_by_date(df, "h_opportunity_risk", True)
        df["rank_rr_only"] = df["p_rr"]
        df["rank_rr_momvol"] = np.sqrt(df["p_rr"].clip(0, 1) * df["p_momvol"].clip(0, 1))
        df["rank_rr_trenddd"] = np.sqrt(df["p_rr"].clip(0, 1) * df["p_trenddd"].clip(0, 1))
        df["rank_rr_opportunity"] = np.sqrt(df["p_rr"].clip(0, 1) * df["p_opp"].clip(0, 1))
        df["rank_rr_quality4"] = (df["p_rr"] * df["p_momvol"] * df["p_trenddd"] * df["p_opp"]).clip(lower=0) ** 0.25

    candidates = ["rank_rr_only", "rank_rr_momvol", "rank_rr_trenddd", "rank_rr_opportunity", "rank_rr_quality4"]
    val_rows = []
    for c in candidates:
        val_rows += evaluate(valid, c, c)
    val = pd.DataFrame(val_rows)
    v5 = val[val["top_k_per_signal_date"] == 5].copy()
    v5["pf_sort"] = pd.to_numeric(v5["profit_factor"], errors="coerce").fillna(0)
    v5 = v5.sort_values(["expectancy", "stop_rate", "pf_sort", "big_winner_rate"], ascending=[False, True, False, False])
    chosen = str(v5.iloc[0]["ranking"])

    hold_rows = []
    for c in ["rank_rr_only", chosen]:
        hold_rows += evaluate(hold, c, c)
    out = pd.DataFrame(hold_rows)
    report = {
        "version": "V22.1_HYBRID_RANKING_RR_2",
        "status": "READY",
        "rr_target_source": "GOVERNED_OHLC_HIGH_PRIOR_126_TRADING_SESSIONS_SHIFT_1",
        "rr_formula": "(prior_high_126s / signal_close - 1) / stop_pct_known_at_signal",
        "target_lookback_sessions": TARGET_LOOKBACK_SESSIONS,
        "coverage_train": coverage_train,
        "coverage_holdout": coverage_hold,
        "chosen_ranking_pre2023": chosen,
        "holdout_used_for_tuning": False,
        "selection_rule": "pre2023 validation Top5: expectancy desc, stop rate asc, PF desc, big-winner rate desc",
        "anti_lookahead": "target high uses shift(1); no signal-day high, future MFE, future return, or future entry open used in RR",
    }
    val.to_csv(args.out_dir / "HYBRID_RANKING_VALIDATION.csv", index=False)
    out.to_csv(args.out_dir / "HYBRID_RANKING_HOLDOUT.csv", index=False)
    (args.out_dir / "HYBRID_RANKING_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
