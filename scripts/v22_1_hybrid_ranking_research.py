from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

HOLDOUT_START = pd.Timestamp("2023-01-01")
EMBARGO = pd.Timedelta(weeks=26)
STOP_DEFAULT = 0.09
TOP_K = (1, 3, 5)
BIG_WIN = 0.15
TARGET_WINDOWS = (20, 63, 126)


def n(s):
    return pd.to_numeric(s, errors="coerce")


def load_prior_high_targets(price_path: Path) -> pd.DataFrame:
    p = pd.read_parquet(price_path)
    p.columns = [str(c).strip().lower() for c in p.columns]
    date_col = next((c for c in ("date", "market_data_date", "as_of_date") if c in p.columns), None)
    if date_col is None or "isin" not in p.columns or "high" not in p.columns:
        raise SystemExit("BLOCK_RR_TARGET_DATA: governed OHLC history lacks isin/date/high")
    p["date"] = pd.to_datetime(p[date_col], errors="coerce").dt.normalize().astype("datetime64[ns]")
    p["high"] = n(p["high"])
    p = p.dropna(subset=["isin", "date", "high"])
    p = p.sort_values(["isin", "date"], kind="stable").drop_duplicates(["isin", "date"], keep="last")
    mins = {20: 10, 63: 20, 126: 40}
    for w in TARGET_WINDOWS:
        p[f"prior_high_{w}s"] = p.groupby("isin", sort=False)["high"].transform(
            lambda s, w=w: s.shift(1).rolling(w, min_periods=mins[w]).max()
        )
    cols = ["isin", "date"] + [f"prior_high_{w}s" for w in TARGET_WINDOWS]
    return p[cols].dropna(subset=[f"prior_high_{w}s" for w in TARGET_WINDOWS], how="all")


def attach_target(raw: pd.DataFrame, target_hist: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x["date"] = pd.to_datetime(x["as_of_date"], errors="coerce").dt.normalize().astype("datetime64[ns]")
    x["_row_id"] = np.arange(len(x))
    left = x.dropna(subset=["isin", "date"]).sort_values(["date", "isin"], kind="stable")
    right = target_hist.sort_values(["date", "isin"], kind="stable")
    merged = pd.merge_asof(left, right, on="date", by="isin", direction="backward", allow_exact_matches=True)
    target_cols = [f"prior_high_{w}s" for w in TARGET_WINDOWS]
    out = x.merge(merged[["_row_id"] + target_cols], on="_row_id", how="left", validate="one_to_one")
    return out.drop(columns=["_row_id"])


def add_features(raw: pd.DataFrame) -> pd.DataFrame:
    x = raw.copy()
    x["date"] = pd.to_datetime(x["as_of_date"], errors="coerce").dt.normalize()
    close = n(x["close"])
    stop_pct = n(x["stop_pct_used"]) if "stop_pct_used" in x else pd.Series(STOP_DEFAULT, index=x.index)
    stop_pct = stop_pct.where(np.isfinite(stop_pct) & (stop_pct > 0), STOP_DEFAULT)
    target_candidates = pd.DataFrame(index=x.index)
    for w in TARGET_WINDOWS:
        t = n(x[f"prior_high_{w}s"])
        target_candidates[str(w)] = t.where(np.isfinite(t) & np.isfinite(close) & (close > 0) & (t > close))
    target = target_candidates.min(axis=1, skipna=True)
    valid_target = target.notna() & np.isfinite(target) & np.isfinite(close) & (close > 0) & (target > close)
    upside = pd.Series(np.nan, index=x.index, dtype=float)
    upside.loc[valid_target] = target.loc[valid_target] / close.loc[valid_target] - 1.0
    x["rr_target"] = target
    x["rr_ex_ante"] = upside / stop_pct
    x["rr_target_valid"] = valid_target
    x["ret26"] = n(x["forward_ret_true_26w"])
    x["stop"] = x["hit_stop"].astype("boolean")
    return x


def pct_rank_by_date(df: pd.DataFrame, col: str) -> pd.Series:
    return df.groupby("date")[col].rank(pct=True, ascending=True, method="average")


def metrics(g: pd.DataFrame) -> dict:
    g = g[g["ret26"].notna() & g["stop"].notna() & g["rr_ex_ante"].notna()]
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
        "mean_rr_ex_ante": float(g["rr_ex_ante"].mean()),
        "median_rr_ex_ante": float(g["rr_ex_ante"].median()),
        "p95_rr_ex_ante": float(g["rr_ex_ante"].quantile(0.95)),
    }


def evaluate(df: pd.DataFrame) -> list[dict]:
    z = df.dropna(subset=["date", "rr_ex_ante", "ret26", "stop"]).copy()
    z["rank_rr_only"] = pct_rank_by_date(z, "rr_ex_ante")
    z = z.sort_values(["date", "rank_rr_only"], ascending=[True, False], kind="stable")
    rows = []
    for k in TOP_K:
        top = z.groupby("date", sort=False).head(k)
        rows.append({"ranking": "rank_rr_only", "top_k_per_signal_date": k, **metrics(top)})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--price-parquet", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    targets = load_prior_high_targets(args.price_parquet)
    train_raw = attach_target(pd.read_csv(args.input_dir / "V22_1_TRAIN.csv", low_memory=False), targets)
    train = add_features(train_raw)
    cutoff = HOLDOUT_START - EMBARGO
    train = train[train["date"] < cutoff].copy()
    if train.empty or train["date"].max() >= cutoff:
        raise SystemExit("BLOCK_RR_EMBARGO: pre-2023 26-week embargo violated")
    valid_rr = train["rr_ex_ante"].notna() & np.isfinite(train["rr_ex_ante"]) & (train["rr_ex_ante"] > 0)
    coverage = float(valid_rr.mean())
    n_valid = int(valid_rr.sum())
    if n_valid < 1000:
        raise SystemExit(f"BLOCK_RR_TARGET_COVERAGE: valid_positive_rr={n_valid} coverage={coverage:.4f}")
    split = int(len(train) * 0.80)
    valid = train.iloc[split:].copy()
    val = pd.DataFrame(evaluate(valid))
    if val.empty or int(val["n"].max()) < 100:
        raise SystemExit("BLOCK_RR_VALIDATION: insufficient valid RR observations")
    if (pd.to_numeric(val["mean_rr_ex_ante"], errors="coerce") <= 0).any():
        raise SystemExit("BLOCK_RR_SIGN: non-positive RR survived validation")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    val.to_csv(args.out_dir / "RR_EX_ANTE_VALIDATION_PRE2023.csv", index=False)
    signal_cols = [c for c in ["date", "isin", "ticker", "rr_target", "rr_ex_ante", "rr_target_valid"] if c in train.columns]
    train.loc[valid_rr, signal_cols].to_csv(args.out_dir / "RR_EX_ANTE_SIGNALS_PRE2023.csv", index=False)
    report = {
        "version": "V22.1_RR_EX_ANTE_PIT_4_NEAREST_RESISTANCE",
        "status": "READY",
        "rr_target_source": "NEAREST_OVERHEAD_OF_PRIOR_HIGH_20_63_126_SESSIONS_SHIFT_1",
        "rr_formula": "(nearest_valid_prior_high / signal_close - 1) / stop_pct_known_at_signal",
        "target_validity": "nearest prior 20/63/126-session high strictly greater than signal_close; otherwise RR unavailable fail-closed",
        "target_windows_sessions": list(TARGET_WINDOWS),
        "coverage_pre2023_positive_rr": coverage,
        "valid_positive_rr_count": n_valid,
        "holdout_accessed": False,
        "holdout_scope": "SEALED_UNTIL_FINAL_FROZEN_EVALUATION",
        "embargo_weeks": 26,
        "train_max_date": str(train["date"].max().date()),
        "signal_artifact": "RR_EX_ANTE_SIGNALS_PRE2023.csv",
        "anti_lookahead": "all target highs use shift(1); no signal-day high, future MFE, future return, future entry open, or 2023-2026 holdout used for RR construction/selection",
    }
    (args.out_dir / "RR_EX_ANTE_REPORT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(val.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
