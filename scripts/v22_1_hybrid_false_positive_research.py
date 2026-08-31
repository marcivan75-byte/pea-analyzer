from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from v22_1_hybrid_ranking_research import add_features as add_rr_features
from v22_1_hybrid_ranking_research import attach_target, load_prior_high_targets

HOLDOUT_START = pd.Timestamp("2023-01-01")
EMBARGO = pd.Timedelta(weeks=26)
BIG_WIN = 0.15
MIN_KEEP = 0.50
MIN_BIG_RECALL = 0.90
MAX_EXPECTANCY_GIVEBACK = 0.0025
MIN_PF_RATIO = 0.98
KEEP_LEVELS = (0.50, 0.60, 0.70, 0.80, 0.90)


def num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        raise SystemExit(f"BLOCK_HYBRID_DATA: missing {col}")
    return pd.to_numeric(df[col], errors="coerce")


def build(df: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    x["date"] = pd.to_datetime(df["as_of_date"], errors="coerce").dt.normalize()
    x["ret26"] = num(df, "forward_ret_true_26w")
    x["stop"] = df["hit_stop"].astype("boolean")
    mom = num(df, "mom_26w")
    dd = num(df, "drawdown_4w")
    rsi = num(df, "rsi_14_hebdo")
    atr = num(df, "atr_14_pct")
    close = num(df, "close")
    sma200 = num(df, "sma200")
    trend = close / sma200 - 1.0
    eps = 0.01
    x["H_MOM_VOL"] = mom / (atr.abs() + eps)
    x["H_TREND_DD"] = trend - dd.abs()
    x["H_RSI_TREND"] = ((rsi - 50.0) / 25.0) * trend
    x["H_VOL_DD"] = atr * dd.abs()
    x["H_MOM_DD"] = mom * (1.0 - dd.abs())
    x["H_TREND_VOL"] = trend / (atr.abs() + eps)
    x["H_OPPORTUNITY_RISK"] = mom / (atr.abs() + dd.abs() + eps)
    rr = pd.to_numeric(df.get("rr_ex_ante"), errors="coerce")
    x["H_RR_EX_ANTE"] = np.log1p(rr.where(np.isfinite(rr) & (rr > 0)))

    technical = ["H_MOM_VOL", "H_TREND_DD", "H_RSI_TREND", "H_VOL_DD", "H_MOM_DD", "H_TREND_VOL", "H_OPPORTUNITY_RISK"]
    valid = x["date"].notna() & x["ret26"].notna() & x["stop"].notna()
    for c in technical:
        valid &= np.isfinite(x[c])
    out = x.loc[valid].copy()
    out["stop"] = out["stop"].astype(bool)
    return out.sort_values("date", kind="stable")


def metrics(g: pd.DataFrame) -> dict[str, float | int | None]:
    if g.empty:
        return {"n": 0, "win_rate": None, "stop_rate": None, "expectancy": None, "profit_factor": None, "payoff_ratio": None, "big_winners": 0}
    r = g["ret26"].astype(float)
    w, l = r[r > 0], r[r <= 0]
    gp, gl = float(w.sum()), float((-l).sum())
    return {
        "n": int(len(g)),
        "win_rate": float((r > 0).mean()),
        "stop_rate": float(g["stop"].mean()),
        "expectancy": float(r.mean()),
        "profit_factor": float(gp / gl) if gl > 0 else None,
        "payoff_ratio": float(w.mean() / abs(l.mean())) if len(w) and len(l) and l.mean() != 0 else None,
        "big_winners": int((r >= BIG_WIN).sum()),
    }


def percentile_risk(fit: pd.DataFrame, target: pd.DataFrame, col: str) -> tuple[np.ndarray, int, float]:
    vv = fit[[col, "stop"]].dropna()
    if len(vv) < 100:
        return np.ones(len(target), dtype=float), 1, 0.0
    corr = float(vv[col].rank(method="average").corr(vv["stop"].astype(float).rank(method="average")))
    direction = 1 if corr >= 0 else -1
    ref = np.sort(vv[col].to_numpy(float) * direction)
    vals = target[col].to_numpy(float)
    risk = np.ones(len(target), dtype=float)  # missing PIT input fails closed
    ok = np.isfinite(vals)
    risk[ok] = np.searchsorted(ref, vals[ok] * direction, side="right") / max(len(ref), 1)
    return risk, direction, corr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--price-parquet", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    raw = pd.read_csv(args.input_dir / "V22_1_TRAIN.csv", low_memory=False)
    targets = load_prior_high_targets(args.price_parquet)
    raw = add_rr_features(attach_target(raw, targets))
    train = build(raw)

    embargo_cutoff = HOLDOUT_START - EMBARGO
    if train.empty or train["date"].max() >= embargo_cutoff:
        raise SystemExit(f"BLOCK_HYBRID_EMBARGO: train max date must be before {embargo_cutoff.date()}")

    dates = np.array(sorted(train["date"].dropna().unique()))
    if len(dates) < 20:
        raise SystemExit("BLOCK_HYBRID_DATA: insufficient distinct pre2023 dates")
    split_date = pd.Timestamp(dates[max(1, int(len(dates) * 0.80))])
    fit = train[train["date"] < split_date].copy()
    valid = train[train["date"] >= split_date].copy()
    if len(fit) < 1000 or len(valid) < 1000:
        raise SystemExit("BLOCK_HYBRID_DATA: insufficient fit/validation sample")

    hybrids = [c for c in train.columns if c.startswith("H_")]
    base = metrics(valid)
    base_big = max(int(base["big_winners"] or 0), 1)
    base_pf = float(base["profit_factor"] or 0.0)
    base_exp = float(base["expectancy"] or 0.0)

    valid_risk, directions = {}, {}
    for c in hybrids:
        vr, direction, corr = percentile_risk(fit, valid, c)
        valid_risk[c] = vr
        directions[c] = {"risk_direction": direction, "fit_stop_spearman": corr}

    rows = []
    best = None
    candidates = [(c,) for c in hybrids] + list(itertools.combinations(hybrids, 2))
    for combo in candidates:
        risk = np.mean([valid_risk[c] for c in combo], axis=0)
        for keep_level in KEEP_LEVELS:
            keep = risk <= keep_level
            g = valid.loc[keep]
            m = metrics(g)
            keep_rate = float(keep.mean())
            big_recall = float((m["big_winners"] or 0) / base_big)
            pf = float(m["profit_factor"] or 0.0)
            exp = float(m["expectancy"] or -1e9)
            stop_reduction = float(base["stop_rate"] - m["stop_rate"]) if m["stop_rate"] is not None else -1.0
            admissible = keep_rate >= MIN_KEEP and big_recall >= MIN_BIG_RECALL and exp >= base_exp - MAX_EXPECTANCY_GIVEBACK and pf >= base_pf * MIN_PF_RATIO
            row = {"criteria": "+".join(combo), "keep_level": keep_level, "keep_rate": keep_rate, "big_winner_recall_vs_full": big_recall, "stop_reduction": stop_reduction, "admissible": admissible, **m}
            rows.append(row)
            key = (stop_reduction, exp, pf, big_recall, keep_rate)
            if admissible and (best is None or key > best[0]):
                best = (key, combo, keep_level, row)

    if best is None:
        raise SystemExit("BLOCK_HYBRID_MODEL: no hybrid satisfies pre2023 guards")

    combo, keep_level = best[1], best[2]
    report = {
        "version": "V22.1_HYBRID_FP_RESEARCH_3_RR_FIT_VALID",
        "governance": {
            "training_source": "PRE_2023_PIT_ONLY",
            "fit_source": "FIRST_80_PERCENT_OF_DISTINCT_PRE2023_DATES",
            "selection_source": "LAST_20_PERCENT_OF_DISTINCT_PRE2023_DATES",
            "fit_max_date": str(fit["date"].max().date()),
            "validation_min_date": str(valid["date"].min().date()),
            "holdout_accessed": False,
            "holdout_scope": "SEALED_UNTIL_FINAL_PASS6_EVALUATION",
            "embargo_weeks": 26,
            "embargo_cutoff": str(embargo_cutoff.date()),
            "train_max_date": str(train["date"].max().date()),
            "rr_source": "PASS1_NEAREST_OVERHEAD_PRIOR_HIGH_20_63_126_SHIFT1",
            "rr_missing_policy": "FAIL_CLOSED_AS_MAX_RISK_WHEN_USED",
            "big_winner_definition": BIG_WIN,
            "guards": {"min_keep_rate": MIN_KEEP, "min_big_winner_recall": MIN_BIG_RECALL, "max_expectancy_giveback": MAX_EXPECTANCY_GIVEBACK, "min_pf_ratio_vs_full": MIN_PF_RATIO},
        },
        "hybrid_definitions": {
            "H_RR_EX_ANTE": "log1p(pass1_rr_ex_ante)",
            "H_MOM_VOL": "mom_26w/(abs(atr_14_pct)+0.01)",
            "H_TREND_DD": "close_vs_sma200-abs(drawdown_4w)",
            "H_RSI_TREND": "((rsi_14_hebdo-50)/25)*close_vs_sma200",
            "H_VOL_DD": "atr_14_pct*abs(drawdown_4w)",
            "H_MOM_DD": "mom_26w*(1-abs(drawdown_4w))",
            "H_TREND_VOL": "close_vs_sma200/(abs(atr_14_pct)+0.01)",
            "H_OPPORTUNITY_RISK": "mom_26w/(abs(atr_14_pct)+abs(drawdown_4w)+0.01)",
        },
        "rr_coverage_fit": float(fit["H_RR_EX_ANTE"].notna().mean()),
        "rr_coverage_validation": float(valid["H_RR_EX_ANTE"].notna().mean()),
        "risk_directions": directions,
        "validation_full": base,
        "selected": {"criteria": list(combo), "keep_level": keep_level, "validation": best[3]},
        "promotion_automatic": False,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["admissible", "stop_reduction", "expectancy"], ascending=[False, False, False]).to_csv(args.out_dir / "HYBRID_FP_GRID.csv", index=False)
    (args.out_dir / "HYBRID_FP_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
