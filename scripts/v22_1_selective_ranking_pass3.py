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
RR_WEIGHTS = (0.01, 0.02, 0.05, 0.08)


def num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        raise SystemExit(f"BLOCK_PASS3_DATA: missing {col}")
    return pd.to_numeric(df[col], errors="coerce")


def build(df: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    x["date"] = pd.to_datetime(df["as_of_date"], errors="coerce").dt.normalize()
    x["ticker"] = df["ticker"].astype(str)
    x["isin"] = df["isin"].astype(str)
    x["ret26"] = num(df, "forward_ret_true_26w")
    x["stop"] = df["hit_stop"].astype("boolean")
    x["governed_score"] = num(df, "governed_score")
    mom = num(df, "mom_26w")
    dd = num(df, "drawdown_4w")
    atr = num(df, "atr_14_pct")
    x["H_MOM_DD"] = mom * (1.0 - dd.abs())
    x["H_VOL_DD"] = atr * dd.abs()
    good = x["date"].notna() & x["ret26"].notna() & x["stop"].notna()
    for c in ["governed_score", "H_MOM_DD", "H_VOL_DD"]:
        good &= np.isfinite(x[c])
    x = x.loc[good].copy()
    x["stop"] = x["stop"].astype(bool)
    return x.sort_values(["date", "ticker", "isin"], kind="stable")


def pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(method="average", pct=True)


def metrics(g: pd.DataFrame) -> dict:
    if g.empty:
        return {"n": 0, "win_rate": None, "stop_rate": None, "expectancy": None, "profit_factor": None, "payoff_ratio": None, "big_winners": 0, "rr_coverage": None, "median_rr_ex_ante": None, "mean_rr_ex_ante": None}
    r = g["ret26"].astype(float)
    w, l = r[r > 0], r[r <= 0]
    gp, gl = float(w.sum()), float((-l).sum())
    rr = pd.to_numeric(g.get("rr_ex_ante"), errors="coerce") if "rr_ex_ante" in g else pd.Series(np.nan, index=g.index)
    return {"n": int(len(g)), "win_rate": float((r > 0).mean()), "stop_rate": float(g["stop"].mean()), "expectancy": float(r.mean()), "profit_factor": float(gp / gl) if gl > 0 else None, "payoff_ratio": float(w.mean() / abs(l.mean())) if len(w) and len(l) and l.mean() != 0 else None, "big_winners": int((r >= BIG_WIN).sum()), "rr_coverage": float(rr.notna().mean()), "median_rr_ex_ante": float(rr.median()) if rr.notna().any() else None, "mean_rr_ex_ante": float(rr.mean()) if rr.notna().any() else None}


def select_capacity(g: pd.DataFrame, score_col: str) -> pd.DataFrame:
    z = g.dropna(subset=[score_col]).copy()
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
    ap.add_argument("--rr-signals", type=Path, required=True)
    ap.add_argument("--rr-report", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    p2 = json.loads(args.pass2_report.read_text(encoding="utf-8"))
    p1 = json.loads(args.rr_report.read_text(encoding="utf-8"))
    if p2.get("governance", {}).get("holdout_accessed") is not False:
        raise SystemExit("BLOCK_PASS3_GOVERNANCE: pass2 holdout seal not proven")
    if p1.get("holdout_accessed") is not False or int(p1.get("embargo_weeks", 0)) != 26:
        raise SystemExit("BLOCK_PASS3_GOVERNANCE: pass1 RR governance not proven")
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
    risk = (-valid["H_MOM_DD"]).rank(method="average", pct=True)
    valid = valid.loc[risk <= keep_level].copy()

    rr = pd.read_csv(args.rr_signals, low_memory=False)
    rr["date"] = pd.to_datetime(rr["date"], errors="coerce").dt.normalize()
    rr["rr_ex_ante"] = pd.to_numeric(rr["rr_ex_ante"], errors="coerce")
    rr = rr[np.isfinite(rr["rr_ex_ante"]) & (rr["rr_ex_ante"] > 0)].drop_duplicates(["date", "isin"], keep="last")
    valid = valid.merge(rr[["date", "isin", "rr_ex_ante"]], on=["date", "isin"], how="left", validate="many_to_one")

    valid["R_GOV"] = pct_rank(valid["governed_score"])
    valid["R_VOL_DD_GOOD"] = 1.0 - pct_rank(valid["H_VOL_DD"])
    valid["R_STOP_06"] = 0.94 * valid["R_GOV"] + 0.06 * valid["R_VOL_DD_GOOD"]
    valid["R_STOP_08"] = 0.92 * valid["R_GOV"] + 0.08 * valid["R_VOL_DD_GOOD"]
    valid["R_STOP_10"] = 0.90 * valid["R_GOV"] + 0.10 * valid["R_VOL_DD_GOOD"]
    valid["R_RR"] = valid.groupby("date")["rr_ex_ante"].rank(method="average", pct=True)
    rr_variants = []
    for weight in RR_WEIGHTS:
        name = f"R_STOP08_RR_{int(weight*100):02d}"
        valid[name] = (1.0 - weight) * valid["R_STOP_08"] + weight * valid["R_RR"]
        rr_variants.append(name)
    # Explicit objective-threshold diagnostic, never automatically imposed.
    valid["R_STOP08_RR33"] = valid["R_STOP_08"].where(valid["rr_ex_ante"] >= 3.3)

    variants = ["R_GOV", "R_STOP_06", "R_STOP_08", "R_STOP_10"] + rr_variants + ["R_STOP08_RR33"]
    selected = {v: select_capacity(valid, v) for v in variants}
    base = selected["R_GOV"]
    base_big = max(int((base["ret26"] >= BIG_WIN).sum()), 1)
    anti_stop_ref = metrics(selected["R_STOP_08"])

    rows = []
    best = None
    for v in variants:
        s = selected[v]
        m = metrics(s)
        big_recall = float(((s["ret26"] >= BIG_WIN).sum()) / base_big)
        years = max(int(s["date"].dt.year.nunique()), 1)
        monthly_max = int(s.groupby(s["date"].dt.to_period("M")).size().max()) if len(s) else 0
        yearly_max = int(s.groupby(s["date"].dt.year).size().max()) if len(s) else 0
        admissible = big_recall >= MIN_BIG_RECALL_VS_BASELINE and monthly_max <= MAX_PER_MONTH and yearly_max <= MAX_PER_YEAR
        # RR variants may only displace the anti-stop reference if they do not worsen stops.
        if v in rr_variants or v == "R_STOP08_RR33":
            admissible = admissible and float(m["stop_rate"] or 1.0) <= float(anti_stop_ref["stop_rate"] or 1.0)
        row = {"variant": v, "admissible": admissible, "big_winner_recall_vs_capacity_baseline": big_recall, "avg_entries_per_year": float(len(s) / years), "max_entries_month": monthly_max, "max_entries_year": yearly_max, **m}
        rows.append(row)
        key = (-float(m["stop_rate"] or 1.0), float(m["expectancy"] or -1e9), float(m["profit_factor"] or 0.0), float(m["median_rr_ex_ante"] or -1.0), big_recall)
        if admissible and (best is None or key > best[0]):
            best = (key, v, row)
    if best is None:
        raise SystemExit("BLOCK_PASS3_MODEL: no selective ranking satisfies guards")
    best_v = best[1]
    best_sel = selected[best_v]
    base_stop = float(metrics(base)["stop_rate"] or 1.0)
    if float(best[2]["stop_rate"] or 1.0) >= base_stop:
        raise SystemExit("BLOCK_PASS3_MODEL: no stop-rate improvement versus capacity baseline")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.out_dir / "PASS3_VARIANTS.csv", index=False)
    cols = ["date", "ticker", "isin", "ret26", "stop", "governed_score", "H_MOM_DD", "H_VOL_DD", "rr_ex_ante", best_v]
    best_sel[cols].to_csv(args.out_dir / "PASS3_SELECTED_PRE2023.csv", index=False)
    report = {"version": "V22.1_TABPORT_PASS3_SELECTIVE_RANKING_3_RR_AUDITED", "governance": {"holdout_accessed": False, "holdout_scope": "SEALED_UNTIL_FINAL_FROZEN_EVALUATION", "training_source": "PRE_2023_PIT_ONLY", "embargo_weeks": 26, "train_max_date": str(x["date"].max().date()), "pass1_rr_version": p1.get("version"), "pass2_filter": {"criteria": ["H_MOM_DD"], "keep_level": keep_level}, "capacity": {"max_entries_month": MAX_PER_MONTH, "max_entries_year": MAX_PER_YEAR}, "big_winner_guard": {"definition": BIG_WIN, "min_recall_vs_same_capacity_baseline": MIN_BIG_RECALL_VS_BASELINE}, "rr_weights_tested_pre2023": list(RR_WEIGHTS), "rr_3_3_role": "DIAGNOSTIC_ONLY_NOT_FORCED"}, "baseline_capacity": metrics(base), "anti_stop_reference": anti_stop_ref, "selected": {"variant": best_v, "metrics": best[2]}, "variants": rows, "promotion_automatic": False}
    (args.out_dir / "PASS3_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
