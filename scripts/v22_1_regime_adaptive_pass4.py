from __future__ import annotations

import argparse
import json
import re
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
MIN_EXP_RATIO = 0.90
MIN_PF_RATIO = 0.90


def num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        raise SystemExit(f"BLOCK_PASS4_DATA: missing {col}")
    return pd.to_numeric(df[col], errors="coerce")


def build(df: pd.DataFrame) -> pd.DataFrame:
    x = pd.DataFrame(index=df.index)
    x["date"] = pd.to_datetime(df["as_of_date"], errors="coerce").dt.normalize()
    x["ticker"] = df["ticker"].astype(str)
    x["isin"] = df["isin"].astype(str)
    x["ret26"] = num(df, "forward_ret_true_26w")
    x["stop"] = df["hit_stop"].astype("boolean")
    x["gov"] = num(df, "governed_score")
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
    x["trend"] = trend
    x["atr"] = atr
    good = x["date"].notna() & x["ret26"].notna() & x["stop"].notna() & np.isfinite(x["gov"])
    for c in ["H_MOM_VOL", "H_TREND_DD", "H_RSI_TREND", "H_VOL_DD", "H_MOM_DD", "H_TREND_VOL", "H_OPPORTUNITY_RISK", "trend", "atr"]:
        good &= np.isfinite(x[c])
    x = x.loc[good].copy()
    x["stop"] = x["stop"].astype(bool)
    return x.sort_values(["date", "ticker", "isin"], kind="stable")


def met(g: pd.DataFrame) -> dict:
    if g.empty:
        return {"n": 0, "stop_rate": None, "expectancy": None, "profit_factor": None, "win_rate": None, "big_winners": 0}
    r = g["ret26"].astype(float)
    w, l = r[r > 0], r[r <= 0]
    gl = float((-l).sum())
    return {
        "n": int(len(g)),
        "stop_rate": float(g["stop"].mean()),
        "expectancy": float(r.mean()),
        "profit_factor": float(w.sum() / gl) if gl > 0 else None,
        "win_rate": float((r > 0).mean()),
        "big_winners": int((r >= BIG_WIN).sum()),
    }


def cap(g: pd.DataFrame, col: str) -> pd.DataFrame:
    out, mc, yc = [], {}, {}
    for d, grp in g.sort_values(["date", "ticker"], kind="stable").groupby("date", sort=True):
        mo, yr = d.to_period("M"), int(d.year)
        rem = min(MAX_PER_MONTH - mc.get(mo, 0), MAX_PER_YEAR - yc.get(yr, 0))
        if rem <= 0:
            continue
        z = grp.dropna(subset=[col]).sort_values([col, "ticker", "isin"], ascending=[False, True, True], kind="stable").head(min(MAX_PER_SIGNAL_DATE, rem))
        if len(z):
            out.append(z)
            mc[mo] = mc.get(mo, 0) + len(z)
            yc[yr] = yc.get(yr, 0) + len(z)
    return pd.concat(out).sort_values(["date", col, "ticker"], ascending=[True, False, True], kind="stable") if out else g.iloc[:0].copy()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--pass2-report", type=Path, required=True)
    ap.add_argument("--pass3-report", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    p2 = json.loads(a.pass2_report.read_text())
    p3 = json.loads(a.pass3_report.read_text())

    if p2.get("governance", {}).get("holdout_accessed") is not False or p3.get("governance", {}).get("holdout_accessed") is not False:
        raise SystemExit("BLOCK_PASS4_GOVERNANCE: upstream holdout seal absent")
    if p3.get("governance", {}).get("capacity", {}).get("mode") != "CHRONOLOGICAL_NO_RETROSPECTIVE_REORDER":
        raise SystemExit("BLOCK_PASS4_GOVERNANCE: causal pass3 not frozen")
    p3_variant = str(p3.get("selected", {}).get("variant", ""))
    m = re.fullmatch(r"R_STOP_(\d{2})", p3_variant)
    if not m:
        raise SystemExit(f"BLOCK_PASS4_GOVERNANCE: unsupported pass3 variant {p3_variant}")
    base_w = int(m.group(1)) / 100.0

    criteria = list(p2.get("selected", {}).get("criteria") or [])
    keep = float(p2.get("selected", {}).get("keep_level", 0))
    directions = p2.get("risk_directions", {})
    if not criteria or not (0 < keep <= 1):
        raise SystemExit("BLOCK_PASS4_GOVERNANCE: invalid pass2 filter")

    x = build(pd.read_csv(a.input_dir / "V22_1_TRAIN.csv", low_memory=False))
    cutoff = HOLDOUT_START - EMBARGO
    if x.empty or x["date"].max() >= cutoff:
        raise SystemExit("BLOCK_PASS4_EMBARGO")
    for c in criteria:
        if c not in x.columns:
            raise SystemExit(f"BLOCK_PASS4_GOVERNANCE: unsupported pass2 criterion {c}")

    dates = np.array(sorted(x["date"].dropna().unique()))
    split_date = pd.Timestamp(dates[max(1, int(len(dates) * 0.80))])
    valid = x[x["date"] >= split_date].copy()
    good_parts = []
    for c in criteria:
        direction = int(directions.get(c, {}).get("risk_direction", 1))
        rank = valid.groupby("date")[c].rank(method="average", pct=True, ascending=True)
        good_parts.append(rank if direction < 0 else (1.0 - rank))
    pass2_good = pd.concat(good_parts, axis=1).mean(axis=1)
    valid = valid.loc[pass2_good >= (1.0 - keep)].copy()
    if len(valid) < 1000:
        raise SystemExit("BLOCK_PASS4_DATA: insufficient validation after pass2 filter")

    valid["pgov"] = valid.groupby("date")["gov"].rank(method="average", pct=True)
    valid["pvold_good"] = 1.0 - valid.groupby("date")["H_VOL_DD"].rank(method="average", pct=True)
    valid["STATIC"] = (1.0 - base_w) * valid["pgov"] + base_w * valid["pvold_good"]

    tq1, tq2 = valid["trend"].quantile([1 / 3, 2 / 3])
    aq1, aq2 = valid["atr"].quantile([1 / 3, 2 / 3])
    wt = np.clip(base_w + np.where(valid["trend"] <= tq1, 0.10, np.where(valid["trend"] >= tq2, -0.10, 0.0)), 0.20, 0.80)
    wv = np.clip(base_w + np.where(valid["atr"] >= aq2, 0.10, np.where(valid["atr"] <= aq1, -0.10, 0.0)), 0.20, 0.80)
    wc = np.clip((wt + wv) / 2.0, 0.20, 0.80)
    wr = np.clip(base_w + 0.10 * (valid["trend"] <= tq1) + 0.10 * (valid["atr"] >= aq2), base_w, 0.80)
    weak = np.clip(base_w + 0.15 * (valid["trend"] <= tq1) + 0.15 * (valid["atr"] >= aq2), base_w, 0.85)

    valid["TREND_ADAPT"] = (1 - wt) * valid["pgov"] + wt * valid["pvold_good"]
    valid["VOL_ADAPT"] = (1 - wv) * valid["pgov"] + wv * valid["pvold_good"]
    valid["COMBINED_ADAPT"] = (1 - wc) * valid["pgov"] + wc * valid["pvold_good"]
    valid["RISK_ADD_ADAPT"] = (1 - wr) * valid["pgov"] + wr * valid["pvold_good"]
    valid["WEAK_RISK_ADAPT"] = (1 - weak) * valid["pgov"] + weak * valid["pvold_good"]

    variants = ["STATIC", "TREND_ADAPT", "VOL_ADAPT", "COMBINED_ADAPT", "RISK_ADD_ADAPT", "WEAK_RISK_ADAPT"]
    sels = {v: cap(valid, v) for v in variants}
    base = sels["STATIC"]
    bm = met(base)
    basebig = max(bm["big_winners"], 1)
    rows, best = [], None
    for v in variants:
        s, mm = sels[v], met(sels[v])
        br = mm["big_winners"] / basebig
        adm = br >= MIN_BIG_RECALL and mm["expectancy"] >= bm["expectancy"] * MIN_EXP_RATIO and mm["profit_factor"] >= bm["profit_factor"] * MIN_PF_RATIO
        row = {"variant": v, "admissible": bool(adm), "big_winner_recall_vs_static": float(br), **mm}
        rows.append(row)
        key = (-float(mm["stop_rate"]), float(mm["expectancy"]), float(mm["profit_factor"]), float(br))
        if adm and (best is None or key > best[0]):
            best = (key, v, row)
    if best is None or best[1] == "STATIC" or best[2]["stop_rate"] >= bm["stop_rate"]:
        raise SystemExit("BLOCK_PASS4_MODEL: no causal adaptive stop improvement")

    out = a.out_dir
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "PASS4_VARIANTS.csv", index=False)
    sels[best[1]].to_csv(out / "PASS4_SELECTED_PRE2023.csv", index=False)
    report = {
        "version": "V22.1_TABPORT_PASS4_CAUSAL_REGIME_4_CHAINED",
        "governance": {
            "holdout_accessed": False,
            "holdout_scope": "SEALED_UNTIL_FINAL_FROZEN_EVALUATION",
            "training_source": "PRE_2023_PIT_ONLY",
            "embargo_weeks": 26,
            "train_max_date": str(x["date"].max().date()),
            "validation_min_date": str(valid["date"].min().date()),
            "upstream_pass2_version": p2.get("version"),
            "upstream_pass3_version": p3.get("version"),
            "pass2_filter": {"criteria": criteria, "keep_level": keep, "application": "SAME_AS_PASS3_WITHIN_SIGNAL_DATE_DIRECTIONAL_GOODNESS"},
            "pass3_static_variant": p3_variant,
            "pass3_static_antistop_weight": base_w,
            "ranking_scope": "WITHIN_SIGNAL_DATE_ONLY",
            "capacity": {"mode": "CHRONOLOGICAL_NO_RETROSPECTIVE_REORDER", "max_per_signal_date": 2, "max_entries_month": 5, "max_entries_year": 40},
            "regime_inputs": ["close/sma200-1", "atr_14_pct"],
            "regime_threshold_source": "PRE2023_VALIDATION_TERCILES",
        },
        "regime_thresholds": {"trend_q33": float(tq1), "trend_q67": float(tq2), "atr_q33": float(aq1), "atr_q67": float(aq2)},
        "static": bm,
        "selected": {"variant": best[1], "metrics": best[2]},
        "variants": rows,
        "promotion_automatic": False,
    }
    (out / "PASS4_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
