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
MAX_PER_SIGNAL_DATE = 2
MIN_BIG_RECALL = 0.90
ANTI_WEIGHTS = (0.10, 0.20, 0.30, 0.40, 0.50, 0.60)
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
    mom, dd, atr = num(df, "mom_26w"), num(df, "drawdown_4w"), num(df, "atr_14_pct")
    rsi, close, sma200 = num(df, "rsi_14_hebdo"), num(df, "close"), num(df, "sma200")
    trend = close / sma200 - 1.0
    eps = 0.01
    x["H_MOM_VOL"] = mom / (atr.abs() + eps)
    x["H_TREND_DD"] = trend - dd.abs()
    x["H_RSI_TREND"] = ((rsi - 50.0) / 25.0) * trend
    x["H_VOL_DD"] = atr * dd.abs()
    x["H_MOM_DD"] = mom * (1.0 - dd.abs())
    x["H_TREND_VOL"] = trend / (atr.abs() + eps)
    x["H_OPPORTUNITY_RISK"] = mom / (atr.abs() + dd.abs() + eps)
    good = x["date"].notna() & x["ret26"].notna() & x["stop"].notna() & np.isfinite(x["governed_score"])
    for c in ["H_MOM_VOL", "H_TREND_DD", "H_RSI_TREND", "H_VOL_DD", "H_MOM_DD", "H_TREND_VOL", "H_OPPORTUNITY_RISK"]:
        good &= np.isfinite(x[c])
    x = x.loc[good].copy()
    x["stop"] = x["stop"].astype(bool)
    return x.sort_values(["date", "ticker", "isin"], kind="stable")


def metrics(g: pd.DataFrame) -> dict:
    if g.empty:
        return {"n": 0, "win_rate": None, "stop_rate": None, "expectancy": None, "profit_factor": None, "payoff_ratio": None, "big_winners": 0, "rr_coverage": None, "median_rr_ex_ante": None, "mean_rr_ex_ante": None}
    r = g["ret26"].astype(float)
    w, l = r[r > 0], r[r <= 0]
    gl = float((-l).sum())
    rr = pd.to_numeric(g["rr_ex_ante"], errors="coerce") if "rr_ex_ante" in g else pd.Series(np.nan, index=g.index)
    return {
        "n": int(len(g)),
        "win_rate": float((r > 0).mean()),
        "stop_rate": float(g["stop"].mean()),
        "expectancy": float(r.mean()),
        "profit_factor": float(w.sum() / gl) if gl > 0 else None,
        "payoff_ratio": float(w.mean() / abs(l.mean())) if len(w) and len(l) and l.mean() != 0 else None,
        "big_winners": int((r >= BIG_WIN).sum()),
        "rr_coverage": float(rr.notna().mean()),
        "median_rr_ex_ante": float(rr.median()) if rr.notna().any() else None,
        "mean_rr_ex_ante": float(rr.mean()) if rr.notna().any() else None,
    }


def causal_capacity(g: pd.DataFrame, col: str) -> pd.DataFrame:
    out, mc, yc = [], {}, {}
    for d, grp in g.sort_values(["date", "ticker"], kind="stable").groupby("date", sort=True):
        mo, yr = d.to_period("M"), int(d.year)
        rem = min(MAX_PER_MONTH - mc.get(mo, 0), MAX_PER_YEAR - yc.get(yr, 0))
        if rem <= 0:
            continue
        take = grp.dropna(subset=[col]).sort_values([col, "ticker", "isin"], ascending=[False, True, True], kind="stable").head(min(MAX_PER_SIGNAL_DATE, rem))
        if len(take):
            out.append(take)
            mc[mo] = mc.get(mo, 0) + len(take)
            yc[yr] = yc.get(yr, 0) + len(take)
    return pd.concat(out).sort_values(["date", col, "ticker"], ascending=[True, False, True], kind="stable") if out else g.iloc[:0].copy()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--pass2-report", type=Path, required=True)
    ap.add_argument("--rr-signals", type=Path, required=True)
    ap.add_argument("--rr-report", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()

    p2 = json.loads(a.pass2_report.read_text())
    p1 = json.loads(a.rr_report.read_text())
    if p2.get("governance", {}).get("holdout_accessed") is not False or p1.get("holdout_accessed") is not False or int(p1.get("embargo_weeks", 0)) != 26:
        raise SystemExit("BLOCK_PASS3_GOVERNANCE")
    criteria = list(p2.get("selected", {}).get("criteria") or [])
    keep = float(p2.get("selected", {}).get("keep_level", 0))
    if not criteria or not (0 < keep <= 1):
        raise SystemExit("BLOCK_PASS3_GOVERNANCE: invalid pass2 selection")

    x = build(pd.read_csv(a.input_dir / "V22_1_TRAIN.csv", low_memory=False))
    cutoff = HOLDOUT_START - EMBARGO
    if x.empty or x["date"].max() >= cutoff:
        raise SystemExit("BLOCK_PASS3_EMBARGO")
    for c in criteria:
        if c not in x.columns:
            raise SystemExit(f"BLOCK_PASS3_GOVERNANCE: unsupported pass2 criterion {c}")

    dates = np.array(sorted(x["date"].dropna().unique()))
    split_date = pd.Timestamp(dates[max(1, int(len(dates) * 0.80))])
    valid = x[x["date"] >= split_date].copy()
    if len(valid) < 1000:
        raise SystemExit("BLOCK_PASS3_DATA: insufficient validation")

    good_parts = []
    directions = p2.get("risk_directions", {})
    for c in criteria:
        direction = int(directions.get(c, {}).get("risk_direction", 1))
        rank = valid.groupby("date")[c].rank(method="average", pct=True, ascending=True)
        good_parts.append(rank if direction < 0 else (1.0 - rank))
    pass2_good = pd.concat(good_parts, axis=1).mean(axis=1)
    valid = valid.loc[pass2_good >= (1.0 - keep)].copy()

    rr = pd.read_csv(a.rr_signals, low_memory=False)
    rr["date"] = pd.to_datetime(rr["date"], errors="coerce").dt.normalize()
    rr["rr_ex_ante"] = pd.to_numeric(rr["rr_ex_ante"], errors="coerce")
    rr = rr[np.isfinite(rr["rr_ex_ante"]) & (rr["rr_ex_ante"] > 0)].drop_duplicates(["date", "isin"])
    valid = valid.merge(rr[["date", "isin", "rr_ex_ante"]], on=["date", "isin"], how="left", validate="many_to_one")

    valid["R_GOV"] = valid.groupby("date")["governed_score"].rank(method="average", pct=True)
    valid["R_VOL_DD_GOOD"] = 1.0 - valid.groupby("date")["H_VOL_DD"].rank(method="average", pct=True)
    variants = ["R_GOV"]
    for w in ANTI_WEIGHTS:
        n = f"R_STOP_{int(w * 100):02d}"
        valid[n] = (1 - w) * valid["R_GOV"] + w * valid["R_VOL_DD_GOOD"]
        variants.append(n)

    sels = {v: causal_capacity(valid, v) for v in variants}
    base = sels["R_GOV"]
    base_big = max(int((base["ret26"] >= BIG_WIN).sum()), 1)
    rows, best = [], None
    for v in variants:
        s, m = sels[v], metrics(sels[v])
        br = float((s["ret26"] >= BIG_WIN).sum() / base_big)
        mm = int(s.groupby(s["date"].dt.to_period("M")).size().max()) if len(s) else 0
        ym = int(s.groupby(s["date"].dt.year).size().max()) if len(s) else 0
        adm = br >= MIN_BIG_RECALL and mm <= MAX_PER_MONTH and ym <= MAX_PER_YEAR
        row = {"variant": v, "admissible": bool(adm), "big_winner_recall_vs_capacity_baseline": br, "max_entries_month": mm, "max_entries_year": ym, **m}
        rows.append(row)
        key = (-float(m["stop_rate"] or 1), float(m["expectancy"] or -1e9), float(m["profit_factor"] or 0), br, float(m["median_rr_ex_ante"] or -1))
        if adm and (best is None or key > best[0]):
            best = (key, v, row)
    if best is None or best[1] == "R_GOV":
        raise SystemExit("BLOCK_PASS3_MODEL: no causal anti-stop improvement")

    anti = best[1]
    anti_sel = sels[anti]
    anti_m = metrics(anti_sel)
    valid["R_RR"] = valid.groupby("date")["rr_ex_ante"].rank(method="average", pct=True)
    rr_rows = []
    for w in RR_WEIGHTS:
        n = f"{anti}_RR_{int(w * 100):02d}"
        valid[n] = (1 - w) * valid[anti] + w * valid["R_RR"]
        s = causal_capacity(valid, n)
        m = metrics(s)
        br = float((s["ret26"] >= BIG_WIN).sum() / base_big)
        adm = br >= MIN_BIG_RECALL and float(m["stop_rate"] or 1) <= float(anti_m["stop_rate"] or 1)
        row = {"variant": n, "admissible": bool(adm), "big_winner_recall_vs_capacity_baseline": br, **m}
        rr_rows.append(row)
        if adm:
            key = (-float(m["stop_rate"] or 1), float(m["expectancy"] or -1e9), float(m["profit_factor"] or 0), br, float(m["median_rr_ex_ante"] or -1))
            if key > best[0]:
                best = (key, n, row)
                sels[n] = s

    valid[f"{anti}_RR33"] = valid[anti].where(valid["rr_ex_ante"] >= 3.3)
    s33 = causal_capacity(valid, f"{anti}_RR33")
    rr_rows.append({"variant": f"{anti}_RR33", "admissible": False, "role": "DIAGNOSTIC_ONLY_NOT_FORCED", **metrics(s33)})

    bestv = best[1]
    bestsel = sels.get(bestv, anti_sel)
    if float(metrics(bestsel)["stop_rate"] or 1) >= float(metrics(base)["stop_rate"] or 1):
        raise SystemExit("BLOCK_PASS3_MODEL: stop improvement absent")

    a.out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows + rr_rows).to_csv(a.out_dir / "PASS3_VARIANTS.csv", index=False)
    scorecol = bestv if bestv in bestsel.columns else anti
    cols = ["date", "ticker", "isin", "ret26", "stop", "governed_score", "H_MOM_VOL", "H_MOM_DD", "H_VOL_DD", "rr_ex_ante", scorecol]
    bestsel[cols].to_csv(a.out_dir / "PASS3_SELECTED_PRE2023.csv", index=False)
    report = {
        "version": "V22.1_TABPORT_PASS3_CAUSAL_5_GENERIC_PASS2",
        "governance": {
            "holdout_accessed": False,
            "holdout_scope": "SEALED_UNTIL_FINAL_FROZEN_EVALUATION",
            "training_source": "PRE_2023_PIT_ONLY",
            "embargo_weeks": 26,
            "train_max_date": str(x["date"].max().date()),
            "validation_min_date": str(valid["date"].min().date()),
            "pass1_rr_version": p1.get("version"),
            "pass2_filter": {"criteria": criteria, "keep_level": keep, "application": "WITHIN_SIGNAL_DATE_DIRECTIONAL_GOODNESS_REJECTION"},
            "ranking_scope": "WITHIN_SIGNAL_DATE_ONLY",
            "capacity": {"mode": "CHRONOLOGICAL_NO_RETROSPECTIVE_REORDER", "max_per_signal_date": MAX_PER_SIGNAL_DATE, "max_entries_month": MAX_PER_MONTH, "max_entries_year": MAX_PER_YEAR},
            "anti_stop_weights_tested_pre2023": list(ANTI_WEIGHTS),
            "rr_weights_tested_pre2023": list(RR_WEIGHTS),
        },
        "baseline_capacity": metrics(base),
        "selected": {"variant": bestv, "metrics": metrics(bestsel)},
        "variants": rows + rr_rows,
        "promotion_automatic": False,
    }
    (a.out_dir / "PASS3_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
