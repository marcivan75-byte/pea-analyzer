from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

HOLDOUT_START = pd.Timestamp("2023-01-01")
EMBARGO = pd.Timedelta(weeks=26)
BIG_WIN = 0.15
MAX_PER_MONTH = 5
MAX_PER_YEAR = 40
MIN_BIG_RECALL_VS_BASELINE = 0.90


def num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        raise SystemExit(f"BLOCK_PASS3_DATA: missing {col}")
    return pd.to_numeric(df[col], errors="coerce")


def build(df: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    x["date"] = pd.to_datetime(df["as_of_date"], errors="coerce")
    x["ticker"] = df["ticker"].astype(str)
    x["isin"] = df["isin"].astype(str)
    x["ret26"] = num(df, "forward_ret_true_26w")
    x["stop"] = df["hit_stop"].astype("boolean")
    x["governed_score"] = num(df, "governed_score")
    mom = num(df, "mom_26w")
    dd = num(df, "drawdown_4w")
    x["H_MOM_DD"] = mom * (1.0 - dd.abs())
    good = x["date"].notna() & x["ret26"].notna() & x["stop"].notna()
    for c in ["governed_score", "H_MOM_DD"]:
        good &= np.isfinite(x[c])
    x = x.loc[good].copy()
    x["stop"] = x["stop"].astype(bool)
    return x.sort_values(["date", "ticker", "isin"], kind="stable")


def pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(method="average", pct=True)


def metrics(g: pd.DataFrame) -> dict:
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


def select_capacity(g: pd.DataFrame, score_col: str) -> pd.DataFrame:
    z = g.copy()
    z["month"] = z["date"].dt.to_period("M")
    z["year"] = z["date"].dt.year
    z = z.sort_values(["month", score_col, "date", "ticker"], ascending=[True, False, True, True], kind="stable")
    z = z.groupby("month", sort=True, group_keys=False).head(MAX_PER_MONTH)
    z = z.sort_values(["year", score_col, "date", "ticker"], ascending=[True, False, True, True], kind="stable")
    z = z.groupby("year", sort=True, group_keys=False).head(MAX_PER_YEAR)
    return z.sort_values(["date", score_col, "ticker"], ascending=[True, False, True], kind="stable")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--pass2-report", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    p2 = json.loads(args.pass2_report.read_text(encoding="utf-8"))
    if p2.get("governance", {}).get("holdout_accessed") is not False:
        raise SystemExit("BLOCK_PASS3_GOVERNANCE: pass2 holdout seal not proven")
    if p2.get("selected", {}).get("criteria") != ["H_MOM_DD"]:
        raise SystemExit("BLOCK_PASS3_GOVERNANCE: unsupported pass2 criterion")
    keep_level = float(p2["selected"]["keep_level"])

    x = build(pd.read_csv(args.input_dir / "V22_1_TRAIN.csv", low_memory=False))
    cutoff = HOLDOUT_START - EMBARGO
    if x.empty or x["date"].max() >= cutoff:
        raise SystemExit("BLOCK_PASS3_EMBARGO: pre2023 embargo violated")

    split = int(len(x) * 0.80)
    valid = x.iloc[split:].copy()
    if len(valid) < 1000:
        raise SystemExit("BLOCK_PASS3_DATA: insufficient validation")

    # Reproduce pass2 H_MOM_DD risk direction (-1): low H_MOM_DD is risky.
    risk = (-valid["H_MOM_DD"]).rank(method="average", pct=True)
    valid = valid.loc[risk <= keep_level].copy()

    valid["R_GOV"] = pct_rank(valid["governed_score"])
    valid["R_HYBRID"] = pct_rank(valid["H_MOM_DD"])
    valid["R_BALANCED"] = 0.70 * valid["R_GOV"] + 0.30 * valid["R_HYBRID"]
    valid["R_HYBRID_HEAVY"] = 0.50 * valid["R_GOV"] + 0.50 * valid["R_HYBRID"]

    variants = ["R_GOV", "R_BALANCED", "R_HYBRID_HEAVY"]
    selected = {v: select_capacity(valid, v) for v in variants}
    base = selected["R_GOV"]
    base_big = max(int((base["ret26"] >= BIG_WIN).sum()), 1)

    rows = []
    best = None
    for v in variants:
        s = selected[v]
        m = metrics(s)
        big_recall = float(((s["ret26"] >= BIG_WIN).sum()) / base_big)
        years = max(int(s["date"].dt.year.nunique()), 1)
        per_year = float(len(s) / years)
        monthly_max = int(s.groupby(s["date"].dt.to_period("M")).size().max()) if len(s) else 0
        yearly_max = int(s.groupby(s["date"].dt.year).size().max()) if len(s) else 0
        admissible = big_recall >= MIN_BIG_RECALL_VS_BASELINE and monthly_max <= MAX_PER_MONTH and yearly_max <= MAX_PER_YEAR
        row = {"variant": v, "admissible": admissible, "big_winner_recall_vs_capacity_baseline": big_recall, "avg_entries_per_year": per_year, "max_entries_month": monthly_max, "max_entries_year": yearly_max, **m}
        rows.append(row)
        # Selectivity objective: fewer stops first, then expectancy, PF, big-winner retention.
        key = (-float(m["stop_rate"] or 1.0), float(m["expectancy"] or -1e9), float(m["profit_factor"] or 0.0), big_recall)
        if admissible and (best is None or key > best[0]):
            best = (key, v, row)

    if best is None:
        raise SystemExit("BLOCK_PASS3_MODEL: no selective ranking satisfies guards")

    best_v = best[1]
    best_sel = selected[best_v]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.out_dir / "PASS3_VARIANTS.csv", index=False)
    best_sel[["date", "ticker", "isin", "ret26", "stop", "governed_score", "H_MOM_DD", best_v]].to_csv(args.out_dir / "PASS3_SELECTED_PRE2023.csv", index=False)
    report = {
        "version": "V22.1_TABPORT_PASS3_SELECTIVE_RANKING_1",
        "governance": {
            "holdout_accessed": False,
            "holdout_scope": "SEALED_UNTIL_FINAL_PASS6_EVALUATION",
            "training_source": "PRE_2023_PIT_ONLY",
            "embargo_weeks": 26,
            "train_max_date": str(x["date"].max().date()),
            "pass2_filter": {"criteria": ["H_MOM_DD"], "keep_level": keep_level},
            "capacity": {"max_entries_month": MAX_PER_MONTH, "max_entries_year": MAX_PER_YEAR},
            "big_winner_guard": {"definition": BIG_WIN, "min_recall_vs_same_capacity_baseline": MIN_BIG_RECALL_VS_BASELINE},
        },
        "baseline_capacity": metrics(base),
        "selected": {"variant": best_v, "metrics": best[2]},
        "variants": rows,
        "promotion_automatic": False,
    }
    (args.out_dir / "PASS3_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
