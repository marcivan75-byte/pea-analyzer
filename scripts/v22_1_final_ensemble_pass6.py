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


def num(df: pd.DataFrame, c: str) -> pd.Series:
    if c not in df.columns:
        raise SystemExit(f"BLOCK_PASS6_DATA: missing {c}")
    return pd.to_numeric(df[c], errors="coerce")


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


def period_label(d: pd.Timestamp) -> str:
    if d < pd.Timestamp("2020-01-01"):
        return "PRE2020"
    if d < pd.Timestamp("2021-01-01"):
        return "2020_STRESS"
    if d < pd.Timestamp("2022-01-01"):
        return "2021"
    return "2022_H1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--pass2-report", type=Path, required=True)
    ap.add_argument("--pass3-report", type=Path, required=True)
    ap.add_argument("--pass4-report", type=Path, required=True)
    ap.add_argument("--pass5-report", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()

    p2 = json.loads(a.pass2_report.read_text(encoding="utf-8"))
    p3 = json.loads(a.pass3_report.read_text(encoding="utf-8"))
    p4 = json.loads(a.pass4_report.read_text(encoding="utf-8"))
    p5 = json.loads(a.pass5_report.read_text(encoding="utf-8"))
    for name, obj in [("pass2", p2), ("pass3", p3), ("pass4", p4), ("pass5", p5)]:
        if obj.get("governance", {}).get("holdout_accessed") is not False:
            raise SystemExit(f"BLOCK_PASS6_GOVERNANCE: {name} holdout seal not proven")
    if int(p4.get("governance", {}).get("embargo_weeks", 0)) != 26:
        raise SystemExit("BLOCK_PASS6_GOVERNANCE: pass4 embargo not proven")
    if p4.get("selected", {}).get("variant") != "RISK_ADD_ADAPT":
        raise SystemExit("BLOCK_PASS6_GOVERNANCE: pass4 variant not frozen")
    p3_variant = str(p4.get("governance", {}).get("pass3_static_variant", ""))
    m = re.fullmatch(r"R_STOP_(\d{2})", p3_variant)
    if not m:
        raise SystemExit("BLOCK_PASS6_GOVERNANCE: pass3 static variant unavailable")
    base_w = int(m.group(1)) / 100.0
    if abs(base_w - float(p4.get("governance", {}).get("pass3_static_antistop_weight", -1))) > 1e-12:
        raise SystemExit("BLOCK_PASS6_GOVERNANCE: pass3 weight mismatch")

    ex = p5.get("execution", {})
    exact_execution = (
        float(ex.get("initial_capital_eur", 0)) == 65000.0
        and float(ex.get("max_line_eur", 0)) == 4500.0
        and int(ex.get("max_lines", 0)) == 12
        and ex.get("integer_shares") is True
        and abs(float(ex.get("fee_each_side", 0)) - 0.002) < 1e-12
        and abs(float(ex.get("stress_slippage_each_side", 0)) - 0.001) < 1e-12
    )
    if not exact_execution or p5.get("robustness", {}).get("slippage_dependency_flag") is not False:
        raise SystemExit("BLOCK_PASS6_GOVERNANCE: pass5 execution/robustness not frozen")

    x = build(pd.read_csv(a.train, low_memory=False))
    cutoff = HOLDOUT_START - EMBARGO
    if x.empty or x["date"].max() >= cutoff:
        raise SystemExit("BLOCK_PASS6_EMBARGO: pre2023 embargo violated")
    dates = np.array(sorted(x["date"].dropna().unique()))
    split_date = pd.Timestamp(dates[max(1, int(len(dates) * 0.80))])
    valid = x[x["date"] >= split_date].copy()

    criteria = list(p2.get("selected", {}).get("criteria") or [])
    keep = float(p2.get("selected", {}).get("keep_level", 0))
    directions = p2.get("risk_directions", {})
    if not criteria or not (0 < keep <= 1):
        raise SystemExit("BLOCK_PASS6_GOVERNANCE: pass2 filter invalid")
    good_parts = []
    for c in criteria:
        if c not in valid.columns:
            raise SystemExit(f"BLOCK_PASS6_DATA: unsupported pass2 criterion {c}")
        direction = int(directions.get(c, {}).get("risk_direction", 1))
        rank = valid.groupby("date")[c].rank(method="average", pct=True, ascending=True)
        good_parts.append(rank if direction < 0 else (1.0 - rank))
    pass2_good = pd.concat(good_parts, axis=1).mean(axis=1)
    valid = valid.loc[pass2_good >= (1.0 - keep)].copy()
    if len(valid) < 1000:
        raise SystemExit("BLOCK_PASS6_DATA: insufficient replay population")

    valid["pgov"] = valid.groupby("date")["gov"].rank(method="average", pct=True)
    valid["pvold_good"] = 1.0 - valid.groupby("date")["H_VOL_DD"].rank(method="average", pct=True)
    valid["STATIC"] = (1.0 - base_w) * valid["pgov"] + base_w * valid["pvold_good"]
    th = p4["regime_thresholds"]
    tq1, aq2 = float(th["trend_q33"]), float(th["atr_q67"])
    wr = np.clip(base_w + 0.10 * (valid["trend"] <= tq1) + 0.10 * (valid["atr"] >= aq2), base_w, 0.80)
    valid["ADAPT"] = (1.0 - wr) * valid["pgov"] + wr * valid["pvold_good"]
    valid["ENS50"] = 0.50 * valid["STATIC"] + 0.50 * valid["ADAPT"]
    valid["ENS75"] = 0.25 * valid["STATIC"] + 0.75 * valid["ADAPT"]

    sels = {v: cap(valid, v) for v in ["ADAPT", "ENS50", "ENS75"]}
    bm = met(sels["ADAPT"])
    p4m = p4.get("selected", {}).get("metrics", {})
    if bm["n"] != int(p4m.get("n", -1)) or abs(bm["stop_rate"] - float(p4m.get("stop_rate", -1))) > 1e-12:
        raise SystemExit("BLOCK_PASS6_REPLAY: exact pass4 replay failed")

    base = sels["ADAPT"].copy()
    base["subperiod"] = base["date"].map(period_label)
    required_periods = sorted(base["subperiod"].unique())
    basebig = max(bm["big_winners"], 1)
    rows, stability_rows, best = [], [], None
    for v in ["ADAPT", "ENS50", "ENS75"]:
        s = sels[v].copy()
        mm = met(s)
        recall = mm["big_winners"] / basebig
        s["subperiod"] = s["date"].map(period_label)
        subs = []
        for name, g in s.groupby("subperiod", sort=True):
            sm = met(g)
            row = {"variant": v, "subperiod": name, **sm}
            subs.append(row)
            stability_rows.append(row)
        present = {q["subperiod"] for q in subs}
        stable = set(required_periods).issubset(present) and all(
            q["expectancy"] is not None and q["expectancy"] > 0 and q["profit_factor"] is not None and q["profit_factor"] > 1.0
            for q in subs if q["subperiod"] in required_periods
        )
        guard = (
            recall >= 0.90
            and mm["expectancy"] >= bm["expectancy"] * 0.90
            and mm["profit_factor"] >= bm["profit_factor"] * 0.90
            and mm["stop_rate"] <= bm["stop_rate"] + 0.01
            and stable
        )
        worst_exp = min(q["expectancy"] for q in subs if q["subperiod"] in required_periods)
        worst_pf = min(q["profit_factor"] for q in subs if q["subperiod"] in required_periods)
        row = {
            "variant": v,
            "admissible": bool(guard),
            "big_winner_recall_vs_adapt": float(recall),
            "worst_subperiod_expectancy": float(worst_exp),
            "worst_subperiod_pf": float(worst_pf),
            **mm,
        }
        rows.append(row)
        key = (float(worst_exp), float(worst_pf), -float(mm["stop_rate"]), float(mm["expectancy"]))
        if guard and (best is None or key > best[0]):
            best = (key, v, row)
    if best is None:
        raise SystemExit("BLOCK_PASS6_STABILITY: no final variant passes all pre2023 guards")

    final_v = best[1]
    final_sel = sels[final_v]
    out = a.out_dir
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "PASS6_ENSEMBLE_VARIANTS.csv", index=False)
    pd.DataFrame(stability_rows).to_csv(out / "PASS6_STABILITY_SUBPERIODS.csv", index=False)
    final_sel.to_csv(out / "PASS6_FINAL_SELECTED_PRE2023.csv", index=False)
    frozen = {
        "model": "TABPORT_V22_1_FINAL",
        "variant": final_v,
        "formula": "ADAPT" if final_v == "ADAPT" else ("0.50*STATIC+0.50*ADAPT" if final_v == "ENS50" else "0.25*STATIC+0.75*ADAPT"),
        "pass2_filter": {"criteria": criteria, "keep_level": keep, "risk_directions": directions},
        "pass3_static_variant": p3_variant,
        "pass3_static_antistop_weight": base_w,
        "adaptive_formula": "base_w + 0.10 if trend<=q33 + 0.10 if atr>=q67; clipped [base_w,0.80]",
        "regime_thresholds": th,
        "capacity": {"max_per_signal_date": 2, "max_entries_month": 5, "max_entries_year": 40, "mode": "CHRONOLOGICAL_NO_RETROSPECTIVE_REORDER"},
        "execution": ex,
        "holdout_accessed_during_selection": False,
        "embargo_weeks": 26,
    }
    (out / "PASS6_FROZEN_CONFIG.json").write_text(json.dumps(frozen, indent=2, sort_keys=True), encoding="utf-8")
    report = {
        "version": "V22.1_TABPORT_PASS6_FINAL_ENSEMBLE_2_EXACT_REPLAY",
        "governance": {
            "holdout_accessed": False,
            "holdout_scope": "SEALED_UNTIL_PASS6_SUCCESS_THEN_FINAL_FROZEN_EVALUATION_ONLY",
            "training_source": "PRE_2023_PIT_ONLY",
            "embargo_weeks": 26,
            "train_max_date": str(x["date"].max().date()),
            "validation_min_date": str(valid["date"].min().date()),
            "survivorship_bias_disclosure_required": True,
            "exact_pass4_replay": True,
            "required_stability_periods": required_periods,
        },
        "adapt_reference": bm,
        "selected": {"variant": final_v, "metrics": best[2]},
        "ensemble_variants": rows,
        "stability_subperiods": stability_rows,
        "frozen_config": "PASS6_FROZEN_CONFIG.json",
        "pass5_base_reference": p5.get("base"),
        "pass5_stress_reference": p5.get("stress"),
        "pass5_robustness_reference": p5.get("robustness"),
        "promotion_automatic": False,
    }
    (out / "PASS6_REPORT.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
